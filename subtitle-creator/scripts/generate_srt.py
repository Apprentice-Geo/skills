from __future__ import annotations

import argparse
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NoReturn

from scripts.runtime_paths import RuntimePaths, add_data_dir_argument

from .process_logging import (
    LoggingSession,
    create_workflow_log_path,
    filesystem_cli,
    get_logger,
    result,
)
from .subtitle_job import (
    DEFAULT_SUBTITLE_OPTIONS,
    SUBTITLE_FILENAME,
    SubtitleJobError,
    atomic_write_json,
    compare_normalized_correction,
    expected_srt_bytes,
    load_job,
    read_json_object,
    validate_job,
    validate_subtitle_options,
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def _positive_seconds(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("seconds must be finite and positive") from error
    if not number.is_finite() or number <= 0:
        raise argparse.ArgumentTypeError("seconds must be finite and positive")
    return number


def _positive_chars(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise argparse.ArgumentTypeError("target-chars must be a positive integer")
    return int(value)


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


def generate_srt(
    job_path: Path,
    *,
    results_dir: Path | None = None,
    target_chars: int | None = None,
    target_seconds: Decimal | float | None = None,
    min_gap: Decimal | float | None = None,
) -> Path:
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    job_path = job_path.resolve()
    if not job_path.is_file():
        raise SubtitleJobError(f"subtitle job is not a regular file: {job_path}")

    job = load_job(job_path)
    validate_job(job_path, job, results_dir=results_dir, allow_stale_derived=True)
    if job["status"] != "transcription_bound":
        raise SubtitleJobError("subtitle job has no bound transcription")

    artifacts = job["artifacts"]
    baseline = read_json_object(Path(artifacts["before_correction"]), decimal_numbers=True)
    normalized_path = Path(artifacts["normalized_transcript"])
    normalized = read_json_object(normalized_path, decimal_numbers=True)
    changed_ids = compare_normalized_correction(baseline, normalized)
    options = dict(job["subtitle_options"] or DEFAULT_SUBTITLE_OPTIONS)
    if target_chars is not None:
        options["target_chars"] = target_chars
    for field, value in (("target_seconds", target_seconds), ("min_gap", min_gap)):
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
                raise SubtitleJobError(f"{field} must be a number")
            options[field] = Decimal(str(value))
    options = validate_subtitle_options(options)
    subtitle_path = (job_path.parent / SUBTITLE_FILENAME).resolve()
    expected_subtitle = expected_srt_bytes(normalized, baseline, options, warn=True)
    _atomic_write(subtitle_path, expected_subtitle)

    job["changed_segment_ids"] = changed_ids
    job["subtitle_options"] = options
    artifacts["subtitle"] = str(subtitle_path)
    validate_job(job_path, job, results_dir=results_dir)
    atomic_write_json(job_path, job)
    return subtitle_path


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Generate and publish an SRT subtitle.")
    parser.add_argument("subtitle_job_path", help="Absolute path to subtitle_job.json.")
    parser.add_argument("--target-chars", type=_positive_chars, help="非空白码点软目标，默认 24。")
    parser.add_argument("--target-seconds", type=_positive_seconds, help="时长软目标，默认 6 秒。")
    parser.add_argument("--min-gap", type=_positive_seconds, help="最小停顿，默认 0.2 秒。")
    add_data_dir_argument(parser)
    try:
        arguments = parser.parse_args(argv)
    except SubtitleJobError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    paths = RuntimePaths.resolve(arguments.data_dir)
    session = LoggingSession(create_workflow_log_path("generate-srt", paths)).start()
    try:
        try:
            subtitle_path = generate_srt(
                Path(arguments.subtitle_job_path),
                results_dir=paths.results_dir,
                target_chars=arguments.target_chars,
                target_seconds=arguments.target_seconds,
                min_gap=arguments.min_gap,
            )
            result(get_logger(__name__), "subtitle: %s", subtitle_path)
            return 0
        except (OSError, SubtitleJobError, TypeError, ValueError) as error:
            session.report_failure(error)
            return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
