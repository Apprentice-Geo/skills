from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from scripts.runtime_paths import RuntimePaths, add_data_dir_argument

from .process_logging import (
    LoggingSession,
    create_workflow_log_path,
    filesystem_cli,
    get_logger,
    result,
)
from .subtitle_job import (
    SCHEMA_VERSION,
    SubtitleJobError,
    load_job,
    publish_transcript,
    sha256_file,
    validate_job_identity,
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


def bind_transcription(
    job_path: Path, manifest_path: Path, *, results_dir: Path | None = None
) -> Path:
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    if not manifest_path.is_absolute():
        raise SubtitleJobError("transcription manifest path must be absolute")
    job_path = job_path.resolve()
    manifest_path = manifest_path.resolve()
    if not job_path.is_file():
        raise SubtitleJobError(f"subtitle job is not a regular file: {job_path}")

    job = load_job(job_path)
    validate_job_identity(job_path, job, results_dir=results_dir)
    if not manifest_path.is_file():
        raise SubtitleJobError(f"transcription manifest is not a regular file: {manifest_path}")
    audio_path = Path(job["audio"]["path"])
    if not audio_path.is_file():
        raise SubtitleJobError(f"job audio is not a regular file: {audio_path}")
    if sha256_file(audio_path) != job["audio"]["id"]:
        raise SubtitleJobError("job audio content no longer matches audio.id")

    normalized = _read_normalized_transcript(manifest_path, job["audio"]["id"])

    baseline_bytes = (
        json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    return publish_transcript(job_path, job, baseline_bytes, results_dir=results_dir)


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(
        description="Bind a completed transcription, replacing local corrections."
    )
    parser.add_argument("subtitle_job_path", help="Absolute path to subtitle_job.json.")
    parser.add_argument(
        "--transcription-manifest",
        required=True,
        help="Absolute path to a complete audio-transcribe manifest.json.",
    )
    add_data_dir_argument(parser)
    try:
        arguments = parser.parse_args(argv)
    except SubtitleJobError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    paths = RuntimePaths.resolve(arguments.data_dir)
    session = LoggingSession(create_workflow_log_path("bind-transcription", paths)).start()
    try:
        try:
            output_path = bind_transcription(
                Path(arguments.subtitle_job_path),
                Path(arguments.transcription_manifest),
                results_dir=paths.results_dir,
            )
            session.move_to(Path(arguments.subtitle_job_path).resolve().parent)
            result(get_logger(__name__), "normalized_transcript: %s", output_path)
            return 0
        except (OSError, SubtitleJobError, TypeError, ValueError) as error:
            session.report_failure(error)
            return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
