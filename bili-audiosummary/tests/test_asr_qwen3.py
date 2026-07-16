import json
import sys
import types
from pathlib import Path

import pytest

import scripts.asr.qwen3 as asr_qwen3
from scripts.process_logging import LoggingSession
from scripts.utils import read_json, write_json


def test_qwen3_disables_temperature_for_greedy_generation(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    asr_model_dir = workspace_tmp_path / "qwen3-asr"
    aligner_model_dir = workspace_tmp_path / "qwen3-aligner"
    for model_dir in (asr_model_dir, aligner_model_dir):
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_bytes(b"weights")
    (asr_model_dir / "generation_config.json").write_text(
        json.dumps(
            {
                "eos_token_id": [151643, 151645],
                "pad_token_id": 151643,
                "do_sample": False,
                "temperature": 0.000001,
            }
        ),
        encoding="utf-8",
    )

    received_kwargs = {}

    class FakeQwen3ASRModel:
        @classmethod
        def from_pretrained(cls, _model_path, **kwargs):
            received_kwargs.update(kwargs)
            return cls()

        def transcribe(self, _audio_path, return_time_stamps):
            assert return_time_stamps is True
            timestamps = types.SimpleNamespace(
                items=[
                    types.SimpleNamespace(
                        text="test",
                        start_time=0.0,
                        end_time=0.4,
                    ),
                    types.SimpleNamespace(
                        text=" transcript",
                        start_time=0.4,
                        end_time=1.0,
                    ),
                ]
            )
            return [types.SimpleNamespace(text="test transcript", time_stamps=timestamps)]

    class FakeGenerationConfig:
        def __init__(self, temperature):
            self.temperature = temperature

        @classmethod
        def from_pretrained(cls, _model_path, **kwargs):
            return cls(temperature=kwargs.get("temperature"))

    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True)
    torch.float16 = object()
    torch.bfloat16 = object()
    transformers = types.ModuleType("transformers")
    transformers.GenerationConfig = FakeGenerationConfig
    qwen_asr = types.ModuleType("qwen_asr")
    qwen_asr.Qwen3ASRModel = FakeQwen3ASRModel
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "qwen_asr", qwen_asr)
    monkeypatch.setattr(asr_qwen3, "QWEN3_ASR_MODEL_DIR", asr_model_dir)
    monkeypatch.setattr(asr_qwen3, "QWEN3_ALIGNER_MODEL_DIR", aligner_model_dir)

    log_path = workspace_tmp_path / "qwen3.log"
    intermediate_path = workspace_tmp_path / "asr_qwen3" / "result.json"
    with LoggingSession(log_path):
        info, _segments = asr_qwen3.transcribe_with_qwen3(
            audio_path,
            "en",
            duration=1.0,
            intermediate_path=intermediate_path,
        )

    generation_config = received_kwargs["generation_config"]
    assert generation_config.temperature is None
    assert info["word_timestamps"] is True
    assert "generation flags are not valid" not in log_path.read_text(encoding="utf-8")
    payload = json.loads(intermediate_path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "source": {
            "path": audio_path.as_posix(),
            "size": 5,
            "mtime": audio_path.stat().st_mtime,
            "duration": 1.0,
        },
        "request": {
            "language": "en",
            "model": asr_model_dir.as_posix(),
            "forced_aligner": aligner_model_dir.as_posix(),
            "device": asr_qwen3.QWEN3_DEVICE_MAP,
            "compute_type": asr_qwen3.QWEN3_DTYPE,
            "batch_size": asr_qwen3.QWEN3_MAX_INFERENCE_BATCH_SIZE,
            "max_new_tokens": asr_qwen3.QWEN3_MAX_NEW_TOKENS,
        },
        "text": "test transcript",
        "word_timestamps": [
            {"text": "test", "start": 0.0, "end": 0.4},
            {"text": " transcript", "start": 0.4, "end": 1.0},
        ],
    }


def test_qwen3_reuses_valid_intermediate_result_without_loading_model(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    intermediate_path = workspace_tmp_path / "asr_qwen3" / "result.json"
    asr_qwen3.write_intermediate_result(
        intermediate_path,
        audio_path,
        "en",
        1.0,
        "ab.",
        [
            asr_qwen3.AlignmentItem("a", 0.0, 0.5),
            asr_qwen3.AlignmentItem("b", 0.5, 1.0),
        ],
    )

    with LoggingSession(workspace_tmp_path / "qwen3-cache.log"):
        info, segments = asr_qwen3.transcribe_with_qwen3(
            audio_path,
            "en",
            duration=1.0,
            intermediate_path=intermediate_path,
        )

    assert info["word_timestamps"] is True
    assert segments == [{"id": 0, "start": 0.0, "end": 1.0, "text": "ab."}]
    assert "[Transcribe] Qwen3 cache: reused asr_qwen3/result.json" in capsys.readouterr().out


def test_qwen3_cache_rejects_missing_timestamp_content(
    workspace_tmp_path: Path,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    intermediate_path = workspace_tmp_path / "result.json"
    identity = asr_qwen3.build_cache_identity(audio_path, "zh", 1.0)
    intermediate_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                **identity,
                "text": "测试",
                "word_timestamps": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        asr_qwen3.load_cached_intermediate_result(
            intermediate_path,
            audio_path,
            "zh",
            1.0,
        )
        is None
    )


@pytest.mark.parametrize(
    "mismatch",
    [
        "schema",
        "source",
        "request",
        "text",
        "timestamp_number",
        "timestamp_order",
    ],
)
def test_qwen3_cache_rejects_invalid_identity_or_content(
    workspace_tmp_path: Path,
    mismatch: str,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    path = workspace_tmp_path / "result.json"
    asr_qwen3.write_intermediate_result(
        path,
        audio_path,
        "en",
        1.0,
        "ab",
        [
            asr_qwen3.AlignmentItem("a", 0.0, 0.5),
            asr_qwen3.AlignmentItem("b", 0.5, 1.0),
        ],
    )
    data = read_json(path)
    if mismatch == "schema":
        data["schema_version"] = 0
    elif mismatch == "source":
        data["source"]["size"] += 1
    elif mismatch == "request":
        data["request"]["language"] = "zh"
    elif mismatch == "text":
        data["text"] = ""
    elif mismatch == "timestamp_number":
        data["word_timestamps"][0]["start"] = "zero"
    else:
        data["word_timestamps"][0]["start"] = 0.1
        data["word_timestamps"][1]["start"] = 0.0
    write_json(path, data)

    assert (
        asr_qwen3.load_cached_intermediate_result(
            path,
            audio_path,
            "en",
            1.0,
        )
        is None
    )


def test_qwen3_cache_rejects_corrupt_json(workspace_tmp_path: Path) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    path = workspace_tmp_path / "result.json"
    path.write_text("{", encoding="utf-8")

    assert (
        asr_qwen3.load_cached_intermediate_result(
            path,
            audio_path,
            "en",
            1.0,
        )
        is None
    )


def test_qwen3_intermediate_result_omits_unavailable_content(
    workspace_tmp_path: Path,
) -> None:
    assert asr_qwen3.build_intermediate_payload("transcript", []) == {
        "text": "transcript"
    }
    intermediate_path = workspace_tmp_path / "asr_qwen3" / "result.json"
    intermediate_path.parent.mkdir()
    intermediate_path.write_text('{"text": "stale"}', encoding="utf-8")

    asr_qwen3.write_intermediate_result(
        intermediate_path,
        workspace_tmp_path / "audio.m4a",
        "en",
        None,
        "",
        [],
    )

    assert not intermediate_path.exists()
