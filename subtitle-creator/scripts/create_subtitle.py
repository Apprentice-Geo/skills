from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, NoReturn

from .subtitle_job import (
    JOB_FILENAME,
    RESULTS_DIR,
    SCHEMA_VERSION,
    SubtitleJobError,
    atomic_write_json,
    read_json_object,
    sha256_file,
    validate_job,
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def create_subtitle_job(audio_argument: str) -> Path:
    audio_path = Path(audio_argument).resolve()
    if not audio_path.is_file():
        raise SubtitleJobError(f"audio path is not a regular file: {audio_path}")

    audio_id = sha256_file(audio_path)
    job_path = (RESULTS_DIR / audio_id / JOB_FILENAME).resolve()
    if job_path.exists():
        job = read_json_object(job_path)
        validate_job(job_path, job)
        if job["audio"]["path"] != str(audio_path):
            job["audio"]["path"] = str(audio_path)
            atomic_write_json(job_path, job)
        return job_path

    job: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": audio_id,
        "status": "needs_transcription",
        "audio": {"path": str(audio_path), "id": audio_id},
        "transcription": None,
        "artifacts": None,
        "changed_segment_ids": [],
    }
    atomic_write_json(job_path, job)
    return job_path


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Create or reuse a content-addressed subtitle job.")
    parser.add_argument("audio_path", help="Path to a local audio file.")
    try:
        job_path = create_subtitle_job(parser.parse_args(argv).audio_path)
    except (OSError, SubtitleJobError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"subtitle_job: {job_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
