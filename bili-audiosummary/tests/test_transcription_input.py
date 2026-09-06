from pathlib import Path
from types import SimpleNamespace

import pytest
from audio_transcribe_contract import ResultValidationError

from scripts import transcription_input


def test_adapter_projects_only_consumer_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    result = SimpleNamespace(
        manifest={"audio": {"id": "a" * 64}, "ignored": "upstream detail"},
        transcript={
            "provider": "faster-whisper",
            "language": "zh",
            "duration": 1.0,
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "文本"}],
            "items": [],
        },
    )
    monkeypatch.setattr(transcription_input, "load_result", lambda _path: result)

    projected = transcription_input.load_transcription(Path("manifest.json"))

    assert projected == transcription_input.TranscriptionInput(
        audio_id="a" * 64,
        language="zh",
        duration=1.0,
        segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "文本"}],
    )


def test_adapter_hides_contract_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_path: Path) -> None:
        raise ResultValidationError("invalid bundle")

    monkeypatch.setattr(transcription_input, "load_result", fail)

    with pytest.raises(
        transcription_input.TranscriptionInputError, match="invalid bundle"
    ):
        transcription_input.load_transcription(Path("manifest.json"))
