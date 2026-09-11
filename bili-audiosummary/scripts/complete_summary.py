from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from scripts.config import SKILL_ROOT
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    error,
    get_logger,
    warning,
)
from scripts.process_logging import result as log_result
from scripts.summary_job import (
    JobValidationError,
    job_lock,
    load_job,
    publish_job,
    resolve_local_path,
)
from scripts.utils import read_json
from scripts.validate_summary import ValidationResult, validate_summary

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and complete a Bilibili summary job."
    )
    parser.add_argument("job", type=Path, help="Path to summary_job.json.")
    return parser.parse_args(argv)


def _validate_source(job_path: Path, job: dict[str, Any]) -> None:
    transcript = job["transcript"]
    transcript_path = resolve_local_path(
        job_path, transcript["path"], "transcript.path"
    )
    if not transcript_path.is_file():
        raise JobValidationError(f"transcript does not exist: {transcript_path}")
    if transcript["source"] == "bilibili_subtitle":
        transcript_payload = read_json(transcript_path)
        if (
            not isinstance(transcript_payload, dict)
            or transcript_payload.get("bvid") != job["video"]["bvid"]
            or transcript_payload.get("source") != "subtitle"
            or not isinstance(transcript_payload.get("segments"), list)
            or not transcript_payload["segments"]
        ):
            raise JobValidationError("subtitle transcript is invalid")
        previous_end = 0.0
        for expected_id, segment in enumerate(transcript_payload["segments"]):
            if not isinstance(segment, dict) or segment.get("id") != expected_id:
                raise JobValidationError("subtitle transcript segment ids are invalid")
            start = segment.get("start")
            end = segment.get("end")
            if (
                not isinstance(start, (int, float))
                or isinstance(start, bool)
                or not isinstance(end, (int, float))
                or isinstance(end, bool)
                or not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or start < previous_end
                or end <= start
            ):
                raise JobValidationError("subtitle transcript timestamps are invalid")
            if not isinstance(segment.get("text"), str) or not segment["text"].strip():
                raise JobValidationError("subtitle transcript text is invalid")
            previous_end = end
        return


def _complete_summary_unlocked(
    job_path: Path,
) -> tuple[dict[str, Any], ValidationResult]:
    job_path = job_path.resolve()
    job = load_job(job_path)
    if job["status"] not in {"prompt_ready", "complete"}:
        raise JobValidationError(
            f"cannot complete summary job from status {job['status']!r}"
        )

    _validate_source(job_path, job)

    prompt_path = resolve_local_path(job_path, job["prompt"]["path"], "prompt.path")
    if not prompt_path.is_file():
        raise JobValidationError(f"summary prompt does not exist: {prompt_path}")
    summary_path = resolve_local_path(
        job_path, job["prompt"]["summary_path"], "prompt.summary_path"
    )
    result = validate_summary(summary_path)
    if not result.ok:
        if job["status"] == "complete":
            job = {**job, "status": "prompt_ready"}
            publish_job(job_path, job)
        return job, result

    if job["status"] != "complete":
        job = {**job, "status": "complete"}
        publish_job(job_path, job)
    return job, result


def complete_summary(
    job_path: Path,
) -> tuple[dict[str, Any], ValidationResult]:
    job_path = job_path.resolve()
    with job_lock(job_path):
        return _complete_summary_unlocked(job_path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log_path = create_timestamped_log_path(SKILL_ROOT / ".cache" / "logs", "complete")
    with LoggingSession(log_path) as session:
        try:
            resolved_job_path = args.job.resolve()
            job, validation = complete_summary(resolved_job_path)
        except (OSError, ValueError) as exc:
            session.report_failure(exc)
            return 1
        if not validation.ok:
            message = "Summary validation failed:\n" + "\n".join(
                f"- {item}" for item in validation.errors
            )
            error(logger, "%s\nFull log: %s", message, session.log_path)
            return 1
        session.move_to(resolved_job_path.parent)
        if validation.warnings:
            log_result(logger, "Summary validation passed with warnings:")
            for item in validation.warnings:
                warning(logger, "- %s", item)
        else:
            log_result(logger, "Summary validation passed.")
        log_result(
            logger,
            "Summary job is %s: %s",
            job["status"],
            resolved_job_path,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
