from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from .subtitle_job import JOB_FILENAME, RESULTS_DIR, SubtitleJobError

JOB_ID_PATTERN = re.compile(r"[0-9a-f]{64}")


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def _is_junction(path: Path) -> bool:
    return bool(getattr(path, "is_junction", lambda: False)())


def remove_subtitle_job(job_path: Path) -> tuple[Path, bool]:
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    if job_path.name != JOB_FILENAME:
        raise SubtitleJobError(f"subtitle job path must end with {JOB_FILENAME}")

    job_dir = job_path.parent
    if job_dir.is_symlink() or _is_junction(job_dir):
        raise SubtitleJobError("subtitle job directory must not be a link or junction")

    results_dir = RESULTS_DIR.resolve()
    resolved_job_dir = job_dir.resolve()
    if (
        resolved_job_dir.parent != results_dir
        or JOB_ID_PATTERN.fullmatch(resolved_job_dir.name) is None
    ):
        raise SubtitleJobError("subtitle job directory is outside the default results directory")

    if not resolved_job_dir.exists():
        return resolved_job_dir / JOB_FILENAME, False
    if not resolved_job_dir.is_dir():
        raise SubtitleJobError(f"subtitle job directory is not a directory: {resolved_job_dir}")

    shutil.rmtree(resolved_job_dir)
    return resolved_job_dir / JOB_FILENAME, True


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Remove one subtitle job and all of its local artifacts.")
    parser.add_argument("subtitle_job_path", help="Absolute path to subtitle_job.json.")
    try:
        job_path, removed = remove_subtitle_job(Path(parser.parse_args(argv).subtitle_job_path))
    except (OSError, SubtitleJobError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    label = "removed_subtitle_job" if removed else "subtitle_job_absent"
    print(f"{label}: {job_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
