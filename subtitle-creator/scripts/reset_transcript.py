from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import NoReturn

from .process_logging import (
    LoggingSession,
    create_workflow_log_path,
    filesystem_cli,
    get_logger,
    result,
)
from .runtime_paths import RuntimePaths, add_data_dir_argument
from .subtitle_job import (
    SubtitleJobError,
    load_job,
    publish_transcript,
    validate_job_identity,
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def reset_transcript(job_path: Path, *, results_dir: Path | None = None) -> Path:
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    job_path = job_path.resolve()
    job = load_job(job_path)
    validate_job_identity(job_path, job, results_dir=results_dir)
    if job["status"] != "transcription_bound":
        raise SubtitleJobError("subtitle job has no bound transcription")
    artifacts = job["artifacts"]
    baseline_bytes = Path(artifacts["before_correction"]).read_bytes()
    if hashlib.sha256(baseline_bytes).hexdigest() != artifacts["before_correction_sha256"]:
        raise SubtitleJobError("before-correction artifact digest mismatch")
    return publish_transcript(job_path, job, baseline_bytes, results_dir=results_dir)


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Reset local text to the bound transcription snapshot.")
    parser.add_argument("subtitle_job_path", help="Absolute path to subtitle_job.json.")
    add_data_dir_argument(parser)
    try:
        arguments = parser.parse_args(argv)
    except SubtitleJobError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    paths = RuntimePaths.resolve(arguments.data_dir)
    session = LoggingSession(create_workflow_log_path("reset-transcript", paths)).start()
    try:
        try:
            job_path = Path(arguments.subtitle_job_path)
            output_path = reset_transcript(job_path, results_dir=paths.results_dir)
            session.move_to(job_path.resolve().parent)
            result(get_logger(__name__), "normalized_transcript: %s", output_path)
            return 0
        except (OSError, SubtitleJobError, TypeError, ValueError) as error:
            session.report_failure(error)
            return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
