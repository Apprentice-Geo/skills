from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, Literal, NoReturn, overload

from .process_logging import (
    LoggingSession,
    create_workflow_log_path,
    detail,
    filesystem_cli,
    get_logger,
    result,
)
from .runtime_paths import RuntimePaths, add_data_dir_argument
from .subtitle_job import (
    SubtitleJobError,
    compare_normalized_correction,
    load_job,
    normalized_srt_segments,
    publish_transcript,
    read_json_object,
    transcript_json,
    validate_job,
    validate_job_identity,
)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SubtitleJobError(message)


def _segment_id(value: str) -> int | str:
    if value == "all":
        return value
    if not value.isascii() or not value.isdecimal():
        raise SubtitleJobError("id must be a non-negative integer or all")
    return int(value)


def _context(value: str) -> int:
    parsed = _segment_id(value)
    if not isinstance(parsed, int):
        raise SubtitleJobError("context must be a non-negative integer")
    return parsed


def _load_bound(job_path: Path, results_dir: Path | None):
    if not job_path.is_absolute():
        raise SubtitleJobError("subtitle job path must be absolute")
    job_path = job_path.resolve()
    job = load_job(job_path)
    validate_job_identity(job_path, job, results_dir=results_dir)
    if job["status"] != "transcription_bound":
        raise SubtitleJobError("subtitle job has no bound transcription")
    artifacts = job["artifacts"]
    baseline_path = Path(artifacts["before_correction"])
    baseline_bytes = baseline_path.read_bytes()
    if hashlib.sha256(baseline_bytes).hexdigest() != artifacts["before_correction_sha256"]:
        raise SubtitleJobError("before-correction artifact digest mismatch")
    baseline = read_json_object(baseline_path, decimal_numbers=True)
    compare_normalized_correction(baseline, baseline)
    normalized_srt_segments(baseline)
    return job_path, job, baseline_bytes, baseline


@overload
def operate(
    command: Literal["show"],
    job_path: Path,
    segment_id: int | str,
    *,
    context: int | None = None,
    text: str | None = None,
    results_dir: Path | None = None,
) -> dict[str, Any]: ...


@overload
def operate(
    command: Literal["edit", "reset"],
    job_path: Path,
    segment_id: int | str,
    *,
    context: int | None = None,
    text: str | None = None,
    results_dir: Path | None = None,
) -> Path: ...


def operate(
    command: str,
    job_path: Path,
    segment_id: int | str,
    *,
    context: int | None = None,
    text: str | None = None,
    results_dir: Path | None = None,
) -> Path | dict[str, Any]:
    if command not in {"show", "edit", "reset"}:
        raise SubtitleJobError("unsupported transcript command")
    if segment_id != "all" and (type(segment_id) is not int or segment_id < 0):
        raise SubtitleJobError("id must be a non-negative integer or all")
    if context is not None and (type(context) is not int or context < 0):
        raise SubtitleJobError("context must be a non-negative integer")
    if context is not None and (command != "show" or segment_id == "all"):
        raise SubtitleJobError("context requires show with an integer id")
    if command == "edit" and (segment_id == "all" or not isinstance(text, str) or not text.strip()):
        raise SubtitleJobError("edit requires an integer id and non-blank text")
    job_path, job, baseline_bytes, baseline = _load_bound(job_path, results_dir)
    if segment_id != "all" and segment_id >= len(baseline["segments"]):
        raise SubtitleJobError("segment id does not exist")
    if command == "reset" and segment_id == "all":
        return publish_transcript(job_path, job, baseline_bytes, results_dir=results_dir)
    validate_job(job_path, job, results_dir=results_dir, allow_stale_derived=True)
    normalized = read_json_object(
        Path(job["artifacts"]["normalized_transcript"]), decimal_numbers=True
    )
    segments = normalized["segments"]
    if command == "show":
        if isinstance(segment_id, int):
            count = context or 0
            segments = segments[max(0, segment_id - count) : segment_id + count + 1]
        return {"target_id": None if segment_id == "all" else segment_id, "segments": segments}
    assert isinstance(segment_id, int)
    segments[segment_id]["text"] = (
        text if command == "edit" else baseline["segments"][segment_id]["text"]
    )
    return publish_transcript(
        job_path,
        job,
        baseline_bytes,
        normalized_bytes=(transcript_json(normalized) + "\n").encode("utf-8"),
        results_dir=results_dir,
    )


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="查看、编辑或恢复本地转写分段。")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("show", "edit", "reset"):
        child = commands.add_parser(command)
        child.add_argument("subtitle_job_path", help="subtitle_job.json 的绝对路径。")
        child.add_argument("--id", required=True, type=_segment_id)
        add_data_dir_argument(child)
        if command == "show":
            child.add_argument("--context", type=_context, default=None)
        if command == "edit":
            child.add_argument("--text", required=True)
    try:
        arguments = parser.parse_args(argv)
        if arguments.command == "show" and arguments.id == "all" and arguments.context is not None:
            raise SubtitleJobError("--id all forbids --context")
        if arguments.command == "edit" and (arguments.id == "all" or not arguments.text.strip()):
            raise SubtitleJobError("edit requires an integer id and non-blank text")
    except SubtitleJobError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    paths = RuntimePaths.resolve(arguments.data_dir)
    session = LoggingSession(create_workflow_log_path("transcript", paths)).start()
    try:
        try:
            job_path = Path(arguments.subtitle_job_path)
            output = operate(
                arguments.command,
                job_path,
                arguments.id,
                context=getattr(arguments, "context", None),
                text=getattr(arguments, "text", None),
                results_dir=paths.results_dir,
            )
            session.move_to(job_path.resolve().parent)
            if isinstance(output, dict):
                detail(get_logger(__name__), "show: %s segments", len(output["segments"]))
                print(transcript_json(output))
            else:
                detail(
                    get_logger(__name__),
                    "%s: %s segments",
                    arguments.command,
                    1 if arguments.id != "all" else len(read_json_object(output)["segments"]),
                )
                result(get_logger(__name__), "normalized_transcript: %s", output)
            return 0
        except (OSError, SubtitleJobError, TypeError, ValueError) as error:
            session.report_failure(error)
            return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
