from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from audio_transcribe_contract import load_result

from scripts.run_pipeline import write_summary_prompt
from scripts.summary_job import (
    JobValidationError,
    TranscriptionValidationError,
    job_lock,
    load_job,
    publish_job,
    relative_path,
    resolve_local_path,
)
from scripts.transcript_output import render_markdown
from scripts.utils import write_text_atomic


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continue a Bilibili summary job with an external transcription."
    )
    parser.add_argument("job", type=Path, help="Path to summary_job.json.")
    parser.add_argument(
        "--transcription-manifest",
        type=Path,
        required=True,
        help="Absolute path to audio-transcribe result_manifest.json.",
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
) -> tuple[Any, str]:
    result = load_result(manifest_path)
    audio_path = resolve_local_path(
        job_path, job["resources"]["audio"], "resources.audio"
    )
    if _file_sha256(audio_path) != result.manifest["audio"]["id"]:
        raise TranscriptionValidationError(
            "transcription result audio does not match the summary job"
        )
    payload = {
        "title": job["video"]["title"],
        "bvid": job["video"]["bvid"],
        "url": job["video"]["url"],
        "uploader": job["video"].get("uploader"),
        "duration": result.transcript["duration"],
        "source": "audio_transcribe",
        "language": result.transcript["language"],
        "segments": result.transcript["segments"],
    }
    return result, render_markdown(payload)


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
        raise TranscriptionValidationError(
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
        recorded_manifest = Path(job["transcription_manifest"]).resolve()
        if recorded_manifest != manifest_path:
            raise JobValidationError(
                "summary job already references a different transcription manifest"
            )
        result_dir = job_path.parent
        markdown_path = result_dir / "transcript.md"
        prompt_path = resolve_local_path(job_path, job["prompt"]["path"], "prompt.path")
        if markdown_path.is_file() and prompt_path.is_file():
            return job
        transcript_language = None
        if not markdown_path.is_file():
            result, markdown = _validated_transcript_markdown(
                job_path, job, recorded_manifest
            )
            write_text_atomic(markdown_path, markdown)
            transcript_language = result.transcript["language"]
        if prompt_path.is_file():
            return job
        if transcript_language is None:
            summary_path = resolve_local_path(
                job_path, job["prompt"]["summary_path"], "prompt.summary_path"
            )
            transcript_language = summary_path.stem.rsplit("_", maxsplit=1)[-1]
        updated = {
            **job,
            "prompt": _write_prompt(job_path, job, transcript_language),
        }
        publish_job(job_path, updated)
        return updated

    if job["status"] != "needs_transcription":
        raise JobValidationError(
            f"cannot continue summary job from status {job['status']!r}"
        )

    result, markdown = _validated_transcript_markdown(job_path, job, manifest_path)
    write_text_atomic(job_path.parent / "transcript.md", markdown)
    prompt = _write_prompt(job_path, job, result.transcript["language"])
    updated = {
        **job,
        "status": "prompt_ready",
        "transcript": {
            "source": "audio_transcribe",
            "path": str(result.transcript_path),
        },
        "transcription_manifest": str(manifest_path),
        "prompt": prompt,
        "error": None,
    }
    publish_job(job_path, updated)
    return updated


def continue_summary(job_path: Path, manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_absolute():
        raise TranscriptionValidationError(
            "--transcription-manifest must be an absolute path"
        )
    job_path = job_path.resolve()
    with job_lock(job_path):
        return _continue_summary_unlocked(job_path, manifest_path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        job = continue_summary(args.job, args.transcription_manifest)
    except (OSError, ValueError) as exc:
        print(f"Cannot continue summary: {exc}")
        return 1
    print(f"Summary job is {job['status']}: {args.job.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
