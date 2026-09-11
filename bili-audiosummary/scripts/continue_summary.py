from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from scripts.config import SKILL_ROOT
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    get_logger,
    result,
)
from scripts.run_pipeline import write_summary_prompt
from scripts.summary_job import (
    JobValidationError,
    job_lock,
    load_job,
    publish_job,
    relative_path,
    resolve_local_path,
)
from scripts.transcript_output import render_markdown
from scripts.transcription_input import (
    TranscriptionInput,
    TranscriptionInputError,
    load_transcription,
)
from scripts.utils import write_text_atomic

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continue a Bilibili summary job with an external transcription."
    )
    parser.add_argument("job", type=Path, help="Path to summary_job.json.")
    parser.add_argument(
        "--transcription-manifest",
        type=Path,
        required=True,
        help="Absolute path to an audio-transcribe manifest.json.",
    )
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


def _continue_summary_unlocked(job_path: Path, manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_absolute():
        raise TranscriptionInputError(
            "--transcription-manifest must be an absolute path"
        )
    job_path = job_path.resolve()
    manifest_path = manifest_path.resolve()
    job = load_job(job_path)

    if job["status"] in {"prompt_ready", "complete"}:
        if job["transcript"]["source"] != "audio_transcribe":
            raise JobValidationError(
                f"cannot attach transcription to {job['transcript']['source']} job"
            )
        return job

    if job["status"] != "needs_transcription":
        raise JobValidationError(
            f"cannot continue summary job from status {job['status']!r}"
        )

    transcription, markdown = _validated_transcript_markdown(
        job_path, job, manifest_path
    )
    write_text_atomic(job_path.parent / "transcript.md", markdown)
    prompt = _write_prompt(job_path, job, transcription.language)
    updated = {
        **job,
        "status": "prompt_ready",
        "transcript": {
            "source": "audio_transcribe",
            "path": "transcript.md",
        },
        "prompt": prompt,
        "error": None,
    }
    publish_job(job_path, updated)
    return updated


def continue_summary(job_path: Path, manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_absolute():
        raise TranscriptionInputError(
            "--transcription-manifest must be an absolute path"
        )
    job_path = job_path.resolve()
    with job_lock(job_path):
        return _continue_summary_unlocked(job_path, manifest_path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log_path = create_timestamped_log_path(SKILL_ROOT / ".cache" / "logs", "continue")
    with LoggingSession(log_path) as session:
        try:
            resolved_job_path = args.job.resolve()
            job = continue_summary(resolved_job_path, args.transcription_manifest)
            session.move_to(resolved_job_path.parent)
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
