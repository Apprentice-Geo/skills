from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from scripts.config import SKILL_ROOT
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    filesystem_cli,
    get_logger,
    result,
)
from scripts.runtime_paths import RuntimePaths, add_data_dir_argument
from scripts.summary_job import JOB_FILENAME, JobValidationError

VIDEO_ID_PATTERN = re.compile(r"BV[0-9A-Za-z]+(?:_p[1-9][0-9]*)?")
logger = get_logger(__name__)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise JobValidationError(message)


def _is_junction(path: Path) -> bool:
    return bool(getattr(path, "is_junction", lambda: False)())


def remove_summary_job(
    job_path: Path, *, results_dir: Path | None = None
) -> tuple[Path, bool]:
    if not job_path.is_absolute():
        raise JobValidationError("summary job path must be absolute")
    if job_path.name != JOB_FILENAME:
        raise JobValidationError(f"summary job path must end with {JOB_FILENAME}")

    job_dir = job_path.parent
    if job_dir.is_symlink() or _is_junction(job_dir):
        raise JobValidationError("summary job directory must not be a link or junction")

    results_dir = (results_dir or RuntimePaths.resolve().results_dir).resolve()
    resolved_job_dir = job_dir.resolve()
    if (
        resolved_job_dir.parent != results_dir
        or VIDEO_ID_PATTERN.fullmatch(resolved_job_dir.name) is None
    ):
        raise JobValidationError(
            "summary job directory is outside the currently configured results directory"
        )

    if not resolved_job_dir.exists():
        return resolved_job_dir / JOB_FILENAME, False
    if not resolved_job_dir.is_dir():
        raise JobValidationError(
            f"summary job directory is not a directory: {resolved_job_dir}"
        )

    shutil.rmtree(resolved_job_dir)
    return resolved_job_dir / JOB_FILENAME, True


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(
        description="Remove one summary job and all of its local artifacts."
    )
    parser.add_argument("summary_job_path", help="Absolute path to summary_job.json.")
    add_data_dir_argument(parser)
    try:
        args = parser.parse_args(argv)
    except JobValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    paths = RuntimePaths.resolve(args.data_dir, root=SKILL_ROOT)
    log_path = create_timestamped_log_path(paths.logs_dir, "remove")
    with LoggingSession(log_path) as session:
        try:
            job_path, removed = remove_summary_job(
                Path(args.summary_job_path), results_dir=paths.results_dir
            )
        except (OSError, JobValidationError, TypeError, ValueError) as exc:
            session.report_failure(exc)
            return 1
        label = "removed_summary_job" if removed else "summary_job_absent"
        result(logger, "%s: %s", label, job_path)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
