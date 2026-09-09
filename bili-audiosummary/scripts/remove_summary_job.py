from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from scripts.config import RESULTS_DIR
from scripts.summary_job import JOB_FILENAME, JobValidationError

VIDEO_ID_PATTERN = re.compile(r"BV[0-9A-Za-z]+(?:_p[1-9][0-9]*)?")


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise JobValidationError(message)


def _is_junction(path: Path) -> bool:
    return bool(getattr(path, "is_junction", lambda: False)())


def remove_summary_job(job_path: Path) -> tuple[Path, bool]:
    if not job_path.is_absolute():
        raise JobValidationError("summary job path must be absolute")
    if job_path.name != JOB_FILENAME:
        raise JobValidationError(f"summary job path must end with {JOB_FILENAME}")

    job_dir = job_path.parent
    if job_dir.is_symlink() or _is_junction(job_dir):
        raise JobValidationError("summary job directory must not be a link or junction")

    results_dir = RESULTS_DIR.resolve()
    resolved_job_dir = job_dir.resolve()
    if (
        resolved_job_dir.parent != results_dir
        or VIDEO_ID_PATTERN.fullmatch(resolved_job_dir.name) is None
    ):
        raise JobValidationError(
            "summary job directory is outside the default results directory"
        )

    if not resolved_job_dir.exists():
        return resolved_job_dir / JOB_FILENAME, False
    if not resolved_job_dir.is_dir():
        raise JobValidationError(
            f"summary job directory is not a directory: {resolved_job_dir}"
        )

    shutil.rmtree(resolved_job_dir)
    return resolved_job_dir / JOB_FILENAME, True


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(
        description="Remove one summary job and all of its local artifacts."
    )
    parser.add_argument("summary_job_path", help="Absolute path to summary_job.json.")
    try:
        job_path, removed = remove_summary_job(
            Path(parser.parse_args(argv).summary_job_path)
        )
    except (OSError, JobValidationError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    label = "removed_summary_job" if removed else "summary_job_absent"
    print(f"{label}: {job_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
