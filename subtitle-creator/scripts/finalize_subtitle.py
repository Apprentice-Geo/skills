from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

from .process_logging import (
    LoggingSession,
    create_workflow_log_path,
    get_logger,
    result,
)
from .subtitle_job import (
    SUBTITLE_FILENAME,
    SubtitleJobError,
    atomic_write_json,
    compare_normalized_correction,
    expected_srt_bytes,
    read_json_object,
    validate_job,
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def _timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def _atomic_write(path: Path, content: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def finalize_subtitle(job_path: Path) -> Path:
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    job_path = job_path.resolve()
    if not job_path.is_file():
        raise SubtitleJobError(f"subtitle job is not a regular file: {job_path}")

    job = read_json_object(job_path)
    validate_job(job_path, job, allow_stale_derived=True)

    artifacts = job["artifacts"]
    baseline = read_json_object(Path(artifacts["before_correction"]), decimal_numbers=True)
    normalized_path = Path(artifacts["normalized_transcript"])
    normalized = read_json_object(normalized_path, decimal_numbers=True)
    changed_ids = compare_normalized_correction(baseline, normalized)
    subtitle_path = (job_path.parent / SUBTITLE_FILENAME).resolve()
    recorded_subtitle = artifacts["subtitle"]
    expected_subtitle = expected_srt_bytes(normalized)
    if (
        recorded_subtitle == str(subtitle_path)
        and subtitle_path.is_file()
        and subtitle_path.read_bytes() == expected_subtitle
        and job["changed_segment_ids"] == changed_ids
    ):
        return subtitle_path

    _atomic_write(subtitle_path, expected_subtitle)

    job["changed_segment_ids"] = changed_ids
    artifacts["subtitle"] = str(subtitle_path)
    validate_job(job_path, job)
    atomic_write_json(job_path, job)
    return subtitle_path


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Generate and publish an SRT subtitle.")
    parser.add_argument("subtitle_job_path", help="Absolute path to subtitle_job.json.")
    try:
        arguments = parser.parse_args(argv)
    except SubtitleJobError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    session = LoggingSession(create_workflow_log_path("finalize-subtitle")).start()
    try:
        try:
            subtitle_path = finalize_subtitle(Path(arguments.subtitle_job_path))
            session.move_to(subtitle_path.parent)
            result(get_logger(__name__), "subtitle: %s", subtitle_path)
            return 0
        except (OSError, SubtitleJobError, TypeError, ValueError) as error:
            session.report_failure(error)
            return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
