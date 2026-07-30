from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, NoReturn

from .subtitle_job import (
    BEFORE_CORRECTION_FILENAME,
    NORMALIZED_FILENAME,
    SCHEMA_VERSION,
    SubtitleJobError,
    atomic_write_json,
    read_json_object,
    require_sha256,
    sha256_file,
    validate_job,
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def _number(value: object, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SubtitleJobError(f"{field} must be a number")
    if value < 0 or (isinstance(value, float) and not math.isfinite(value)):
        raise SubtitleJobError(f"{field} must be finite and non-negative")
    return value


def _resolve_transcript_path(manifest_path: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise SubtitleJobError("artifacts.transcript must be a non-empty relative path")
    relative_path = Path(value)
    if relative_path.is_absolute():
        raise SubtitleJobError("artifacts.transcript must be a relative path")
    result_dir = manifest_path.parent.resolve()
    transcript_path = (result_dir / relative_path).resolve()
    if not transcript_path.is_relative_to(result_dir):
        raise SubtitleJobError("artifacts.transcript escapes the manifest directory")
    if not transcript_path.is_file():
        raise SubtitleJobError(f"transcript artifact is not a regular file: {transcript_path}")
    return transcript_path


def _read_normalized_transcript(
    manifest_path: Path, manifest: dict[str, Any], audio_id: str
) -> tuple[dict[str, Any], str]:
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != SCHEMA_VERSION
        or manifest.get("status") != "complete"
    ):
        raise SubtitleJobError("manifest is not a complete schema 1 result")
    audio = manifest.get("audio")
    request = manifest.get("request")
    artifacts = manifest.get("artifacts")
    digests = manifest.get("artifact_sha256")
    if not all(isinstance(item, dict) for item in (audio, request, artifacts, digests)):
        raise SubtitleJobError("manifest is missing required objects")
    assert isinstance(audio, dict)
    assert isinstance(request, dict)
    assert isinstance(artifacts, dict)
    assert isinstance(digests, dict)

    manifest_audio_id = require_sha256(audio.get("id"), "manifest.audio.id")
    if manifest_audio_id != audio_id:
        raise SubtitleJobError("manifest audio identity does not match the job")
    variant_id = require_sha256(request.get("variant_id"), "manifest.request.variant_id")
    provider = request.get("provider")
    language = request.get("language")
    if not isinstance(provider, str) or not provider:
        raise SubtitleJobError("manifest.request.provider must be a non-empty string")
    if not isinstance(language, str) or not language:
        raise SubtitleJobError("manifest.request.language must be a non-empty string")
    duration = _number(audio.get("duration"), "manifest.audio.duration")

    transcript_path = _resolve_transcript_path(manifest_path, artifacts.get("transcript"))
    expected_digest = require_sha256(
        digests.get("transcript"), "manifest.artifact_sha256.transcript"
    )
    if sha256_file(transcript_path) != expected_digest:
        raise SubtitleJobError("transcript artifact digest mismatch")
    transcript = read_json_object(transcript_path)
    if (
        type(transcript.get("schema_version")) is not int
        or transcript["schema_version"] != SCHEMA_VERSION
    ):
        raise SubtitleJobError("transcript has an unsupported schema_version")
    if transcript.get("audio_id") != manifest_audio_id:
        raise SubtitleJobError("transcript audio identity does not match the manifest")
    if transcript.get("variant_id") != variant_id:
        raise SubtitleJobError("transcript variant identity does not match the manifest")
    if transcript.get("provider") != provider:
        raise SubtitleJobError("transcript provider does not match the manifest")
    if transcript.get("language") != language:
        raise SubtitleJobError("transcript language does not match the manifest")
    if transcript.get("duration") != duration:
        raise SubtitleJobError("transcript duration does not match the manifest")
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise SubtitleJobError("transcript segments must be an array")

    normalized_segments: list[dict[str, Any]] = []
    for segment in segments:
        if (
            not isinstance(segment, dict)
            or "start" not in segment
            or "end" not in segment
            or not isinstance(segment.get("text"), str)
        ):
            raise SubtitleJobError("transcript contains an invalid segment")
        text = " ".join(segment["text"].split())
        if text:
            normalized_segments.append(
                {
                    "id": len(normalized_segments),
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": text,
                }
            )
    if not normalized_segments:
        raise SubtitleJobError("transcript has no non-empty segments after normalization")

    return (
        {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "manifest_path": str(manifest_path),
                "audio_id": manifest_audio_id,
                "variant_id": variant_id,
            },
            "provider": provider,
            "language": language,
            "duration": duration,
            "segments": normalized_segments,
        },
        variant_id,
    )


def attach_transcription(job_path: Path, manifest_path: Path) -> Path:
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    if not manifest_path.is_absolute():
        raise SubtitleJobError("transcription manifest path must be absolute")
    job_path = job_path.resolve()
    manifest_path = manifest_path.resolve()
    if not job_path.is_file():
        raise SubtitleJobError(f"subtitle job is not a regular file: {job_path}")
    if not manifest_path.is_file():
        raise SubtitleJobError(f"transcription manifest is not a regular file: {manifest_path}")

    job = read_json_object(job_path)
    validate_job(job_path, job)
    audio_path = Path(job["audio"]["path"])
    if not audio_path.is_file():
        raise SubtitleJobError(f"job audio is not a regular file: {audio_path}")
    if sha256_file(audio_path) != job["audio"]["id"]:
        raise SubtitleJobError("job audio content no longer matches audio.id")

    normalized, variant_id = _read_normalized_transcript(
        manifest_path, read_json_object(manifest_path), job["audio"]["id"]
    )
    if job["status"] == "editable":
        if job["transcription"]["variant_id"] != variant_id:
            raise SubtitleJobError("job is already bound to a different transcription variant")
        return Path(job["artifacts"]["normalized_transcript"])

    job_dir = job_path.parent
    baseline_path = (job_dir / BEFORE_CORRECTION_FILENAME).resolve()
    normalized_path = (job_dir / NORMALIZED_FILENAME).resolve()
    atomic_write_json(baseline_path, normalized)
    atomic_write_json(normalized_path, normalized)

    job["status"] = "editable"
    job["transcription"] = {
        "manifest_path": str(manifest_path),
        "variant_id": variant_id,
    }
    job["artifacts"] = {
        "normalized_transcript": str(normalized_path),
        "normalized_transcript_sha256": None,
        "before_correction": str(baseline_path),
        "before_correction_sha256": sha256_file(baseline_path),
        "subtitle": None,
        "subtitle_sha256": None,
    }
    validate_job(job_path, job)
    atomic_write_json(job_path, job)
    return normalized_path


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Attach and normalize a completed transcription.")
    parser.add_argument("subtitle_job_path", help="Absolute path to subtitle_job.json.")
    parser.add_argument(
        "--transcription-manifest",
        required=True,
        help="Absolute path to a complete result_manifest.json.",
    )
    try:
        arguments = parser.parse_args(argv)
        output_path = attach_transcription(
            Path(arguments.subtitle_job_path), Path(arguments.transcription_manifest)
        )
    except (OSError, SubtitleJobError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"normalized_transcript: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
