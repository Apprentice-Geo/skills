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
    JOB_FILENAME,
    JOB_SCHEMA_VERSION,
    SubtitleJobError,
    atomic_write_json,
    load_job,
    read_json_object,
    sha256_file,
    subtitle_matches_normalized,
    validate_job,
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def open_subtitle_job(audio_argument: str, *, results_dir: Path | None = None) -> Path:
    audio_path = Path(audio_argument).resolve()
    if not audio_path.is_file():
        raise SubtitleJobError(f"audio path is not a regular file: {audio_path}")

    audio_id = sha256_file(audio_path)
    results_dir = results_dir or RuntimePaths.resolve().results_dir
    job_path = (results_dir / audio_id / JOB_FILENAME).resolve()
    if job_path.exists():
        job = load_job(job_path)
        # Existing bound jobs may contain a legitimately edited transcript or a
        # damaged derived subtitle. Generation validates the source artifacts and
        # rebuilds those derived values after the caller dispatches on job status.
        try:
            validate_job(job_path, job, results_dir=results_dir, allow_stale_derived=True)
        except SubtitleJobError as error:
            raise SubtitleJobError(f"invalid subtitle job {job_path}: {error}") from error
        if job["audio"]["path"] != str(audio_path):
            job["audio"]["path"] = str(audio_path)
            atomic_write_json(job_path, job)
        return job_path

    job: dict[str, Any] = {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": audio_id,
        "status": "transcription_unbound",
        "audio": {"path": str(audio_path), "id": audio_id},
        "artifacts": None,
        "changed_segment_ids": [],
    }
    atomic_write_json(job_path, job)
    return job_path


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Create or reuse a content-addressed subtitle job.")
    parser.add_argument("audio_path", help="Path to a local audio file.")
    add_data_dir_argument(parser)
    try:
        arguments = parser.parse_args(argv)
    except SubtitleJobError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    paths = RuntimePaths.resolve(arguments.data_dir)
    session = LoggingSession(create_workflow_log_path("open-subtitle-job", paths)).start()
    try:
        try:
            job_path = open_subtitle_job(arguments.audio_path, results_dir=paths.results_dir)
            job = load_job(job_path)
            validate_job(job_path, job, results_dir=paths.results_dir, allow_stale_derived=True)
            artifacts = job["artifacts"]
            normalized_path = artifacts["normalized_transcript"] if artifacts else None
            subtitle_path = artifacts["subtitle"] if artifacts else None
            if (
                subtitle_path is not None
                and normalized_path is not None
                and not subtitle_matches_normalized(
                    Path(subtitle_path),
                    read_json_object(Path(normalized_path), decimal_numbers=True),
                )
            ):
                subtitle_path = None
            summary = {
                "status": job["status"],
                "subtitle_job": str(job_path),
                "audio_path": job["audio"]["path"],
                "normalized_transcript": normalized_path,
                "subtitle": subtitle_path,
            }
            session.move_to(job_path.parent)
            result(get_logger(__name__), "%s", json.dumps(summary, ensure_ascii=False))
            return 0
        except (OSError, SubtitleJobError, TypeError, ValueError) as error:
            session.report_failure(error)
            return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
