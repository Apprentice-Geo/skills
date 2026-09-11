from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audio_transcribe_contract import ResultValidationError, load_result


class TranscriptionInputError(ValueError):
    pass


@dataclass(frozen=True)
class TranscriptionInput:
    audio_id: str
    provider: str
    language: str
    duration: int | float
    segments: list[dict[str, Any]]


def load_transcription(path: Path) -> TranscriptionInput:
    try:
        result = load_result(path)
    except ResultValidationError as exc:
        raise TranscriptionInputError(str(exc)) from exc
    transcript = result.transcript
    return TranscriptionInput(
        audio_id=result.manifest["audio"]["id"],
        provider=transcript["provider"],
        language=transcript["language"],
        duration=transcript["duration"],
        segments=[dict(segment) for segment in transcript["segments"]],
    )
