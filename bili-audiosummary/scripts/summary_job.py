from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from scripts.utils import read_json, write_json_atomic

SCHEMA_VERSION = 1
JOB_FILENAME = "summary_job.json"
JOB_KEYS = {
    "schema_version",
    "status",
    "video",
    "resources",
    "transcript",
    "transcription_manifest",
    "prompt",
    "error",
}
STABLE_STATUSES = {
    "preparing",
    "needs_transcription",
    "prompt_ready",
    "complete",
    "failed",
}


class JobValidationError(ValueError):
    pass


class TranscriptionValidationError(ValueError):
    pass


@contextmanager
def job_lock(job_path: Path) -> Iterator[None]:
    job_path = job_path.resolve()
    job_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = job_path.with_name(f".{job_path.name}.lock")
    stream = lock_path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def relative_path(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def resolve_local_path(job_path: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise JobValidationError(f"{field} must be a non-empty relative path")
    raw_path = Path(value)
    if raw_path.is_absolute():
        raise JobValidationError(f"{field} must be relative to the job directory")
    job_dir = job_path.resolve().parent
    resolved = (job_dir / raw_path).resolve()
    if not resolved.is_relative_to(job_dir):
        raise JobValidationError(f"{field} escapes the job directory")
    return resolved


def resolve_manifest_artifact(
    manifest_path: Path, artifacts: dict[str, Any], name: str
) -> Path:
    value = artifacts.get(name)
    if not isinstance(value, str) or not value:
        raise TranscriptionValidationError(
            f"transcription manifest artifact {name!r} is missing"
        )
    raw_path = Path(value)
    if raw_path.is_absolute():
        raise TranscriptionValidationError(
            f"transcription manifest artifact {name!r} must be relative"
        )
    manifest_dir = manifest_path.resolve().parent
    resolved = (manifest_dir / raw_path).resolve()
    if not resolved.is_relative_to(manifest_dir):
        raise TranscriptionValidationError(
            f"transcription manifest artifact {name!r} escapes its directory"
        )
    return resolved


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JobValidationError(f"{field} must be an object")
    return value


def validate_job(job_path: Path, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != JOB_KEYS:
        raise JobValidationError("summary job has an invalid top-level shape")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise JobValidationError("unsupported summary job schema_version")
    status = payload.get("status")
    if status not in STABLE_STATUSES:
        raise JobValidationError("summary job has an invalid status")

    video = _require_mapping(payload.get("video"), "video")
    for key in ("bvid", "title", "url"):
        if not isinstance(video.get(key), str) or not video[key]:
            raise JobValidationError(f"video.{key} must be a non-empty string")

    resources = _require_mapping(payload.get("resources"), "resources")
    for key in ("fetch_manifest", "subtitle", "audio", "subtitle_skipped"):
        if key not in resources:
            raise JobValidationError(f"resources.{key} is missing")
    if not isinstance(resources["subtitle_skipped"], bool):
        raise JobValidationError("resources.subtitle_skipped must be boolean")
    resolve_local_path(
        job_path, resources["fetch_manifest"], "resources.fetch_manifest"
    )
    for key in ("subtitle", "audio"):
        value = resources[key]
        if value is not None:
            resolve_local_path(job_path, value, f"resources.{key}")

    transcript = payload.get("transcript")
    transcription_manifest = payload.get("transcription_manifest")
    prompt = payload.get("prompt")
    error = payload.get("error")

    if status in {"preparing", "needs_transcription"}:
        if (
            transcript is not None
            or transcription_manifest is not None
            or prompt is not None
        ):
            raise JobValidationError(
                f"{status} job must not contain transcript or prompt"
            )
        if error is not None:
            raise JobValidationError(f"{status} job must not contain an error")
        if status == "needs_transcription" and resources["audio"] is None:
            raise JobValidationError("needs_transcription job requires resources.audio")
    elif status in {"prompt_ready", "complete"}:
        transcript_payload = _require_mapping(transcript, "transcript")
        if set(transcript_payload) != {"source", "path"}:
            raise JobValidationError("transcript has an invalid shape")
        source = transcript_payload.get("source")
        if source == "bilibili_subtitle":
            resolve_local_path(
                job_path, transcript_payload.get("path"), "transcript.path"
            )
            if transcription_manifest is not None:
                raise JobValidationError(
                    "subtitle job must not contain transcription_manifest"
                )
        elif source == "audio_transcribe":
            if (
                not isinstance(transcript_payload.get("path"), str)
                or not Path(transcript_payload["path"]).is_absolute()
            ):
                raise JobValidationError(
                    "audio_transcribe transcript.path must be absolute"
                )
            if (
                not isinstance(transcription_manifest, str)
                or not Path(transcription_manifest).is_absolute()
            ):
                raise JobValidationError(
                    "audio_transcribe transcription_manifest must be absolute"
                )
        else:
            raise JobValidationError("transcript.source is invalid")

        prompt_payload = _require_mapping(prompt, "prompt")
        if set(prompt_payload) != {"path", "summary_path"}:
            raise JobValidationError("prompt has an invalid shape")
        resolve_local_path(job_path, prompt_payload.get("path"), "prompt.path")
        resolve_local_path(
            job_path, prompt_payload.get("summary_path"), "prompt.summary_path"
        )
        if error is not None:
            raise JobValidationError(f"{status} job must not contain an error")
    else:
        if (
            transcript is not None
            or transcription_manifest is not None
            or prompt is not None
        ):
            raise JobValidationError("failed job must not contain transcript or prompt")
        error_payload = _require_mapping(error, "error")
        if set(error_payload) != {"stage", "type", "message"}:
            raise JobValidationError("failed job error has an invalid shape")
        if not all(isinstance(error_payload[key], str) for key in error_payload):
            raise JobValidationError("failed job error fields must be strings")

    return payload


def load_job(job_path: Path) -> dict[str, Any]:
    job_path = job_path.resolve()
    if not job_path.is_file():
        raise JobValidationError(f"summary job does not exist: {job_path}")
    return validate_job(job_path, read_json(job_path))


def publish_job(job_path: Path, payload: dict[str, Any]) -> None:
    validate_job(job_path, payload)
    write_json_atomic(job_path, payload)


def load_transcription(manifest_path: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        raise TranscriptionValidationError(
            f"transcription manifest does not exist: {manifest_path}"
        )
    try:
        manifest = read_json(manifest_path)
    except Exception as exc:
        raise TranscriptionValidationError(
            f"cannot read transcription manifest: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise TranscriptionValidationError("transcription manifest must be an object")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise TranscriptionValidationError(
            "transcription manifest artifacts must be an object"
        )
    transcript_path = resolve_manifest_artifact(manifest_path, artifacts, "transcript")
    if not transcript_path.is_file():
        raise TranscriptionValidationError(
            f"transcription artifact does not exist: {transcript_path}"
        )
    try:
        transcript = read_json(transcript_path)
    except Exception as exc:
        raise TranscriptionValidationError(f"cannot read transcript: {exc}") from exc
    if not isinstance(transcript, dict):
        raise TranscriptionValidationError("transcript must be an object")
    return transcript_path, transcript
