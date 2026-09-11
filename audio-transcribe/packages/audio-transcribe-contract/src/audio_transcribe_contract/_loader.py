from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

from ._types import ResultManifest, TranscriptionResult
from ._validation import (
    ResultValidationError,
    _artifact_path,
    _read_object,
    _read_object_bytes,
    _validate_common_artifact,
    _validate_manifest,
    _validate_transcript,
)


def load_manifest(path: str | Path) -> ResultManifest:
    """Validate metadata and paths only; this does not certify a complete result."""
    try:
        manifest_path = Path(path).resolve()
    except (OSError, RuntimeError, TypeError) as exc:
        raise ResultValidationError("Invalid result manifest path.") from exc
    if not manifest_path.is_file():
        error = FileNotFoundError(manifest_path)
        raise ResultValidationError("Result manifest must be a file.") from error

    manifest_payload = _read_object(manifest_path, "result manifest")
    artifacts, *_ = _validate_manifest(manifest_payload)

    root = manifest_path.parent.resolve()
    transcript_path = _artifact_path(root, artifacts.get("transcript"), "transcript")
    if transcript_path == manifest_path:
        raise ResultValidationError("Transcript must be separate from the manifest.")
    return cast(ResultManifest, manifest_payload)


def load_result(path: str | Path) -> TranscriptionResult:
    """Read and validate a portable bundle, without logs or private workspace files."""
    manifest = load_manifest(path)
    manifest_path = Path(path).resolve()
    transcript_path = _artifact_path(
        manifest_path.parent, manifest["artifacts"]["transcript"], "transcript"
    )
    if not transcript_path.is_file():
        raise ResultValidationError("transcript artifact must be a file.")

    try:
        transcript_bytes = transcript_path.read_bytes()
    except OSError as exc:
        raise ResultValidationError("Unable to read a public artifact.") from exc
    if (
        hashlib.sha256(transcript_bytes).hexdigest()
        != manifest["artifact_sha256"]["transcript"]
    ):
        raise ResultValidationError("transcript artifact digest mismatch.")
    transcript_payload = _read_object_bytes(transcript_bytes, "transcript")
    request = manifest["request"]
    duration = float(manifest["audio"]["duration"])
    _validate_common_artifact(
        transcript_payload,
        name="transcript",
        audio_id=manifest["audio"]["id"],
        config_digest=request["config_digest"],
        provider=request["provider"],
        language=request["language"],
        duration=duration,
    )
    transcript = _validate_transcript(transcript_payload, duration, request["provider"])

    return TranscriptionResult(
        manifest_path=manifest_path,
        transcript_path=transcript_path,
        manifest=manifest,
        transcript=transcript,
    )
