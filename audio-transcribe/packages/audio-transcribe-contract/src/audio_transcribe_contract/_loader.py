from __future__ import annotations

from pathlib import Path
from typing import cast

from ._types import ResultManifest, TranscriptionResult
from ._validation import (
    ResultValidationError,
    _artifact_path,
    _file_sha256,
    _read_object,
    _sha256,
    _validate_common_artifact,
    _validate_manifest,
    _validate_raw_timestamps,
    _validate_transcript,
)


def load_result(path: str | Path) -> TranscriptionResult:
    """Load and validate a transcription result from a manifest path."""
    try:
        manifest_path = Path(path).resolve()
    except (OSError, RuntimeError, TypeError) as exc:
        raise ResultValidationError("Invalid result manifest path.") from exc
    if not manifest_path.is_file():
        error = FileNotFoundError(manifest_path)
        raise ResultValidationError("Result manifest must be a file.") from error

    manifest_payload = _read_object(manifest_path, "result manifest")
    (
        artifacts,
        digests,
        audio_id,
        variant_id,
        provider,
        language,
        duration,
    ) = _validate_manifest(manifest_payload)

    root = manifest_path.parent.resolve()
    transcript_path = _artifact_path(root, artifacts.get("transcript"), "transcript")
    raw_path = _artifact_path(root, artifacts.get("raw_timestamps"), "raw_timestamps")
    log_path = _artifact_path(root, artifacts.get("log"), "log")
    workspace_path = _artifact_path(root, artifacts.get("workspace"), "workspace")
    for name, artifact_path in (
        ("transcript", transcript_path),
        ("raw_timestamps", raw_path),
        ("log", log_path),
    ):
        if not artifact_path.is_file():
            raise ResultValidationError(f"{name} artifact must be a file.")
    if not workspace_path.is_dir():
        raise ResultValidationError("workspace artifact must be a directory.")

    for name, artifact_path in (
        ("transcript", transcript_path),
        ("raw_timestamps", raw_path),
    ):
        expected = _sha256(digests.get(name), f"artifact_sha256.{name}")
        if _file_sha256(artifact_path) != expected:
            raise ResultValidationError(f"{name} artifact digest mismatch.")

    manifest = cast(ResultManifest, manifest_payload)

    transcript_payload = _read_object(transcript_path, "transcript")
    raw_payload = _read_object(raw_path, "raw timestamps")
    for name, payload in (
        ("transcript", transcript_payload),
        ("raw_timestamps", raw_payload),
    ):
        _validate_common_artifact(
            payload,
            name=name,
            audio_id=audio_id,
            variant_id=variant_id,
            provider=provider,
            language=language,
            duration=duration,
        )
    transcript = _validate_transcript(transcript_payload, duration)
    raw_timestamps = _validate_raw_timestamps(raw_payload, duration, provider)

    return TranscriptionResult(
        manifest_path=manifest_path,
        transcript_path=transcript_path,
        raw_timestamps_path=raw_path,
        manifest=manifest,
        transcript=transcript,
        raw_timestamps=raw_timestamps,
    )
