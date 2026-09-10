from __future__ import annotations

import argparse
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
    sha256_file,
    validate_job,
)
from .transcription_input import load_transcription


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def _read_normalized_transcript(manifest_path: Path, audio_id: str) -> dict[str, Any]:
    transcription = load_transcription(manifest_path)
    if transcription.audio_id != audio_id:
        raise SubtitleJobError("manifest audio identity does not match the job")

    normalized_segments: list[dict[str, Any]] = []
    for segment in transcription.segments:
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

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "audio_transcribe",
        "provider": transcription.provider,
        "language": transcription.language,
        "duration": transcription.duration,
        "segments": normalized_segments,
    }


def attach_transcription(job_path: Path, manifest_path: Path) -> Path:
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    if not manifest_path.is_absolute():
        raise SubtitleJobError("transcription manifest path must be absolute")
    job_path = job_path.resolve()
    manifest_path = manifest_path.resolve()
    if not job_path.is_file():
        raise SubtitleJobError(f"subtitle job is not a regular file: {job_path}")

    job = read_json_object(job_path)
    validate_job(job_path, job)
    if job["status"] == "editable":
        return Path(job["artifacts"]["normalized_transcript"])
    if not manifest_path.is_file():
        raise SubtitleJobError(f"transcription manifest is not a regular file: {manifest_path}")
    audio_path = Path(job["audio"]["path"])
    if not audio_path.is_file():
        raise SubtitleJobError(f"job audio is not a regular file: {audio_path}")
    if sha256_file(audio_path) != job["audio"]["id"]:
        raise SubtitleJobError("job audio content no longer matches audio.id")

    normalized = _read_normalized_transcript(manifest_path, job["audio"]["id"])

    job_dir = job_path.parent
    baseline_path = (job_dir / BEFORE_CORRECTION_FILENAME).resolve()
    normalized_path = (job_dir / NORMALIZED_FILENAME).resolve()
    atomic_write_json(baseline_path, normalized)
    atomic_write_json(normalized_path, normalized)

    job["status"] = "editable"
    job["artifacts"] = {
        "normalized_transcript": str(normalized_path),
        "before_correction": str(baseline_path),
        "before_correction_sha256": sha256_file(baseline_path),
        "subtitle": None,
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
        help="Absolute path to a complete audio-transcribe manifest.json.",
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
