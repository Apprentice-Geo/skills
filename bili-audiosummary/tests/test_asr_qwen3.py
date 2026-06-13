import json
import sys
import types
from pathlib import Path

import asr_qwen3
from process_logging import LoggingSession


def test_qwen3_disables_temperature_for_greedy_generation(
    workspace_tmp_path: Path,
    monkeypatch,
    mocker,
) -> None:
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
            timestamps = types.SimpleNamespace(items=[])
            return [types.SimpleNamespace(text="test transcript", time_stamps=timestamps)]

    qwen_asr = types.ModuleType("qwen_asr")
    qwen_asr.Qwen3ASRModel = FakeQwen3ASRModel
    monkeypatch.setitem(sys.modules, "qwen_asr", qwen_asr)
    mocker.patch("torch.cuda.is_available", return_value=True)
    monkeypatch.setattr(asr_qwen3, "QWEN3_ASR_MODEL_DIR", asr_model_dir)
    monkeypatch.setattr(asr_qwen3, "QWEN3_ALIGNER_MODEL_DIR", aligner_model_dir)

    log_path = workspace_tmp_path / "qwen3.log"
    with LoggingSession(log_path):
        asr_qwen3.transcribe_with_qwen3(
            workspace_tmp_path / "audio.m4a",
            "en",
            duration=1.0,
        )

    generation_config = received_kwargs["generation_config"]
    assert generation_config.temperature is None
    assert "generation flags are not valid" not in log_path.read_text(encoding="utf-8")
