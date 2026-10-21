from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.config import SKILL_ROOT
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    filesystem_cli,
    get_logger,
    result,
)
from scripts.runtime_paths import RuntimePaths, add_data_dir_argument
from scripts.summary_job import (
    JobValidationError,
    job_lock,
    load_job,
    publish_job,
    relative_path,
    require_configured_job_path,
    resolve_local_path,
)
from scripts.summary_prompt import select_summary_language, write_summary_prompt
from scripts.transcript_output import render_markdown, write_markdown_from_json
from scripts.transcription_input import (
    TranscriptionInput,
    TranscriptionInputError,
    load_transcription,
)
from scripts.utils import write_text_atomic

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore a summary job, import transcription, or change summary language."
    )
    parser.add_argument("job", type=Path, help="Path to summary_job.json.")
    parser.add_argument(
        "--transcription-manifest",
        type=Path,
        help="Import or replace the transcript from an absolute public manifest path.",
    )
    parser.add_argument("--summary-language", choices=("zh", "en"))
    add_data_dir_argument(parser)
    return parser.parse_args(argv)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_transcript_markdown(
    job_path: Path,
    job: dict[str, Any],
    manifest_path: Path,
) -> tuple[TranscriptionInput, str]:
    transcription = load_transcription(manifest_path)
    audio_path = resolve_local_path(
        job_path, job["resources"]["audio"], "resources.audio"
    )
    if _file_sha256(audio_path) != transcription.audio_id:
        raise TranscriptionInputError(
            "transcription result audio does not match the summary job"
        )
    payload = {
        "title": job["video"]["title"],
        "bvid": job["video"]["bvid"],
        "url": job["video"]["url"],
        "uploader": job["video"].get("uploader"),
        "duration": transcription.duration,
        "source": "audio_transcribe",
        "language": transcription.language,
        "segments": transcription.segments,
    }
    return transcription, render_markdown(payload)


def _write_prompt(
    job_path: Path,
    job: dict[str, Any],
    transcript_language: str | None,
) -> dict[str, str]:
    result_dir = job_path.resolve().parent
    prompt = write_summary_prompt(
        result_dir=result_dir,
        video_id=job["video"]["bvid"],
        transcript_markdown_path=result_dir / "transcript.md",
        summary_language=job["video"].get("summary_language") or transcript_language,
    )
    return {
        "path": relative_path(prompt["prompt_path"], result_dir),
        "summary_path": relative_path(prompt["summary_path"], result_dir),
    }


def _archive_summaries(paths: set[Path]) -> None:
    archived: list[tuple[Path, Path]] = []
    try:
        for path in sorted(paths):
            if path.exists():
                backup = path.with_name(
                    f"{path.stem}.superseded-{uuid4().hex}{path.suffix}"
                )
                path.rename(backup)
                archived.append((path, backup))
    except OSError as exc:
        for original, backup in reversed(archived):
            try:
                backup.rename(original)
            except OSError as rollback_error:
                exc.add_note(
                    f"Summary preserved at {backup}; rollback failed: {rollback_error}"
                )
        raise


def _continue_summary_unlocked(
    job_path: Path,
    manifest_path: Path | None = None,
    *,
    summary_language: str | None = None,
) -> dict[str, Any]:
    if manifest_path is not None and not manifest_path.is_absolute():
        raise TranscriptionInputError(
            "--transcription-manifest must be an absolute path"
        )
    job_path = job_path.resolve()
    job = load_job(job_path)
    if summary_language not in {None, "zh", "en"}:
        raise ValueError("summary language must be zh or en")
    if job["status"] not in {"needs_transcription", "prompt_ready", "complete"}:
        raise JobValidationError(
            f"cannot continue summary job from status {job['status']!r}"
        )

    markdown_path = job_path.parent / "transcript.md"
    transcript = job["transcript"]
    markdown = None
    if manifest_path is not None:
        transcription, markdown = _validated_transcript_markdown(
            job_path, job, manifest_path.resolve()
        )
        transcript = {"source": "audio_transcribe", "path": "transcript.md"}
        default_language = transcription.language
    elif transcript is None:
        if summary_language is not None:
            job = {
                **job,
                "video": {**job["video"], "summary_language": summary_language},
            }
            publish_job(job_path, job)
        return job
    else:
        source_path = resolve_local_path(
            job_path, transcript["path"], "transcript.path"
        )
        if not source_path.is_file():
            raise JobValidationError(f"transcript does not exist: {source_path}")
        if transcript["source"] == "bilibili_subtitle":
            write_markdown_from_json(source_path, markdown_path)
        # 已发布的总结语言是默认语言的唯一来源，不重新读取上游。
        match = re.search(r"_summary_(zh|en)\.md$", job["prompt"]["summary_path"])
        default_language = match.group(1) if match else None

    language = (
        summary_language or job["video"].get("summary_language") or default_language
    )
    selected_language = select_summary_language(language)
    summary_path = (
        job_path.parent / f"{job['video']['bvid']}_summary_{selected_language}.md"
    )
    invalidated = manifest_path is not None or (
        job["prompt"] is not None and job["prompt"]["summary_path"] != summary_path.name
    )
    if invalidated:
        # 先撤销完成状态；状态发布失败时不移动用户总结或改变输入。
        if job["status"] == "complete":
            job = {**job, "status": "prompt_ready"}
            publish_job(job_path, job)
        paths = {summary_path}
        if job["prompt"] is not None:
            paths.add(
                resolve_local_path(
                    job_path, job["prompt"]["summary_path"], "prompt.summary_path"
                )
            )
        _archive_summaries(paths)
    if markdown is not None:
        write_text_atomic(markdown_path, markdown)
    if summary_language is not None:
        job = {**job, "video": {**job["video"], "summary_language": summary_language}}
    prompt = _write_prompt(job_path, job, default_language)
    updated = {
        **job,
        "status": "prompt_ready" if invalidated else job["status"],
        "transcript": transcript,
        "prompt": prompt,
        "error": None,
    }
    publish_job(job_path, updated)
    return updated


def continue_summary(
    job_path: Path,
    manifest_path: Path | None = None,
    *,
    summary_language: str | None = None,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    if manifest_path is not None and not manifest_path.is_absolute():
        raise TranscriptionInputError(
            "--transcription-manifest must be an absolute path"
        )
    require_configured_job_path(
        job_path.absolute(), results_dir or RuntimePaths.resolve().results_dir
    )
    job_path = job_path.resolve()
    with job_lock(job_path):
        return _continue_summary_unlocked(
            job_path, manifest_path, summary_language=summary_language
        )


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = RuntimePaths.resolve(args.data_dir, root=SKILL_ROOT)
    log_path = create_timestamped_log_path(paths.logs_dir, "continue")
    with LoggingSession(log_path) as session:
        try:
            resolved_job_path = args.job.resolve()
            job = continue_summary(
                args.job,
                args.transcription_manifest,
                summary_language=args.summary_language,
                results_dir=paths.results_dir,
            )
        except (OSError, ValueError) as exc:
            session.report_failure(exc)
            return 1
        result(
            logger,
            "Summary job is %s: %s",
            job["status"],
            resolved_job_path,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
