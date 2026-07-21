from __future__ import annotations

import types
from pathlib import Path

import numpy as np
import pytest

import scripts.asr.qwen3 as qwen3
from scripts.asr.chunking import SAMPLE_RATE, NormalizedAudio
from scripts.utils import read_json, write_json_atomic


def make_audio(seconds: int) -> NormalizedAudio:
    samples = np.broadcast_to(np.zeros(1, dtype=np.float32), seconds * SAMPLE_RATE)
    return NormalizedAudio(samples)


def make_result(text: str = "", start: float = 0.0, end: float = 0.0):
    items = (
        []
        if not text
        else [types.SimpleNamespace(text=text, start_time=start, end_time=end)]
    )
    return types.SimpleNamespace(
        text=text, time_stamps=types.SimpleNamespace(items=items)
    )


def prepare(
    monkeypatch, audio: NormalizedAudio, model
) -> tuple[list[Path], list[bool]]:
    decoded: list[Path] = []
    loaded: list[bool] = []
    monkeypatch.setattr(
        qwen3, "decode_normalized_audio", lambda path: decoded.append(path) or audio
    )
    monkeypatch.setattr(qwen3, "detect_speech_samples", lambda *_args: [])
    monkeypatch.setattr(qwen3, "_load_qwen_model", lambda: loaded.append(True) or model)
    return decoded, loaded


def test_qwen3_schema_two_full_cache_skips_decode_cuda_and_model(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr_qwen3"
    plan = qwen3._qwen_build_plan(audio_path, "zh", 25 * SAMPLE_RATE, [])
    write_json_atomic(workspace / "asr_plan.json", plan)
    write_json_atomic(
        workspace / "result.json",
        {
            "schema_version": 2,
            "plan": plan,
            "text": "",
            "word_timestamps": [],
            "segments": [],
        },
    )
    monkeypatch.setattr(
        qwen3, "decode_normalized_audio", lambda *_args: pytest.fail("decoded")
    )
    monkeypatch.setattr(qwen3, "_load_qwen_model", lambda: pytest.fail("loaded"))

    info, segments = qwen3.transcribe_with_qwen3(audio_path, "zh", workspace)

    assert info["max_new_tokens"] == 1024
    assert segments == []


def test_qwen3_uses_constant_driven_full_batches_and_loads_model_once(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    calls = []

    class Model:
        def transcribe(self, inputs, **kwargs):
            calls.append((len(inputs), kwargs))
            return [make_result() for _ in inputs]

    decoded, loaded = prepare(monkeypatch, make_audio(900), Model())
    info, segments = qwen3.transcribe_with_qwen3(
        audio_path, "zh", workspace_tmp_path / "asr_qwen3"
    )

    assert [size for size, _kwargs in calls] == [
        qwen3.QWEN3_MAX_INFERENCE_BATCH_SIZE
    ] * 2
    assert all(kwargs == {"return_time_stamps": True} for _size, kwargs in calls)
    assert len(decoded) == 1
    assert len(loaded) == 1
    assert info["max_new_tokens"] == 1024
    assert segments == []


def test_qwen3_short_full_plan_submits_one_partial_batch(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    batch_sizes = []

    class Model:
        def transcribe(self, inputs, **_kwargs):
            batch_sizes.append(len(inputs))
            return [make_result() for _ in inputs]

    prepare(monkeypatch, make_audio(100), Model())
    qwen3.transcribe_with_qwen3(audio_path, "zh", workspace_tmp_path / "asr_qwen3")
    assert batch_sizes == [3]


def test_qwen3_batch_failure_isolates_members_and_caches_successes(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    calls = []

    class Model:
        def transcribe(self, inputs, **_kwargs):
            calls.append(len(inputs))
            if len(inputs) > 1:
                raise RuntimeError("batch")
            if len(calls) == 3:
                raise RuntimeError("isolated")
            return [make_result()]

    prepare(monkeypatch, make_audio(120), Model())
    workspace = workspace_tmp_path / "asr_qwen3"
    with pytest.raises(RuntimeError, match="chunk_001"):
        qwen3.transcribe_with_qwen3(audio_path, "zh", workspace)

    assert calls == [4, 1, 1, 1, 1]
    assert len(list((workspace / "chunk_results").glob("*.json"))) == 3
    progress = read_json(workspace / "progress.json")
    assert progress["chunks"]["chunk_001"]["status"] == "failed"


def test_qwen3_schema_one_plan_is_rejected_and_empty_chunks_are_cacheable(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr_qwen3"
    write_json_atomic(workspace / "asr_plan.json", {"schema_version": 1})

    class Model:
        def transcribe(self, inputs, **_kwargs):
            return [make_result() for _ in inputs]

    decoded, loaded = prepare(monkeypatch, make_audio(25), Model())
    _info, segments = qwen3.transcribe_with_qwen3(audio_path, "zh", workspace)
    assert segments == []
    assert read_json(workspace / "asr_plan.json")["schema_version"] == 2
    assert len(decoded) == len(loaded) == 1

    monkeypatch.setattr(
        qwen3, "decode_normalized_audio", lambda *_args: pytest.fail("decoded")
    )
    monkeypatch.setattr(qwen3, "_load_qwen_model", lambda: pytest.fail("loaded"))
    assert qwen3.transcribe_with_qwen3(audio_path, "zh", workspace)[1] == []


def test_qwen3_production_max_new_tokens_is_1024() -> None:
    assert qwen3.QWEN3_MAX_NEW_TOKENS == 1024


def test_qwen3_model_loader_keeps_forced_aligner_and_greedy_generation(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    model_dir = workspace_tmp_path / "model"
    aligner_dir = workspace_tmp_path / "aligner"
    for path in (model_dir, aligner_dir):
        path.mkdir()
        (path / "model.safetensors").write_bytes(b"weights")
    received = {}

    class QwenModel:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            received.update(path=path, **kwargs)
            return cls()

    class GenerationConfig:
        @classmethod
        def from_pretrained(cls, _path, **kwargs):
            received["generation_temperature"] = kwargs.get("temperature")
            return cls()

    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True)
    torch.bfloat16 = object()
    qwen_module = types.ModuleType("qwen_asr")
    qwen_module.Qwen3ASRModel = QwenModel
    transformers = types.ModuleType("transformers")
    transformers.GenerationConfig = GenerationConfig
    monkeypatch.setitem(__import__("sys").modules, "torch", torch)
    monkeypatch.setitem(__import__("sys").modules, "qwen_asr", qwen_module)
    monkeypatch.setitem(__import__("sys").modules, "transformers", transformers)
    monkeypatch.setattr(qwen3, "QWEN3_ASR_MODEL_DIR", model_dir)
    monkeypatch.setattr(qwen3, "QWEN3_ALIGNER_MODEL_DIR", aligner_dir)

    qwen3._load_qwen_model()

    assert received["forced_aligner"] == aligner_dir.as_posix()
    assert received["max_inference_batch_size"] == qwen3.QWEN3_MAX_INFERENCE_BATCH_SIZE
    assert received["max_new_tokens"] == 1024
    assert received["generation_temperature"] is None
