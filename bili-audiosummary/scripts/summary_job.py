from __future__ import annotations

import hashlib
import json
import math
import os
import re
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
PUBLIC_PROVIDERS = {"faster-whisper", "qwen3"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _finite_nonnegative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _validate_transcript(
    payload: Any,
    audio_id: str,
    variant_id: str,
    provider: str,
    language: str,
    duration: float,
) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise TranscriptionValidationError("unsupported transcript schema_version")
    if payload.get("audio_id") != audio_id:
        raise TranscriptionValidationError(
            "transcript audio_id does not match manifest"
        )
    if payload.get("variant_id") != variant_id:
        raise TranscriptionValidationError(
            "transcript variant_id does not match manifest"
        )
    if payload.get("provider") != provider:
        raise TranscriptionValidationError(
            "transcript provider does not match manifest"
        )
    if payload.get("language") != language:
        raise TranscriptionValidationError(
            "transcript language does not match manifest"
        )
    if (
        not _finite_nonnegative(payload.get("duration"))
        or payload["duration"] != duration
    ):
        raise TranscriptionValidationError("transcript duration is invalid")

    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise TranscriptionValidationError("transcript segments must be non-empty")
    previous_end = 0.0
    for expected_id, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise TranscriptionValidationError("transcript segment must be an object")
        if segment.get("id") != expected_id:
            raise TranscriptionValidationError(
                "transcript segment ids must be continuous"
            )
        start = segment.get("start")
        end = segment.get("end")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not _finite_nonnegative(start)
            or not _finite_nonnegative(end)
        ):
            raise TranscriptionValidationError("transcript timestamps are invalid")
        numeric_start = float(start)
        numeric_end = float(end)
        if numeric_start < previous_end or numeric_end < numeric_start:
            raise TranscriptionValidationError(
                "transcript timestamps must be monotonic"
            )
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise TranscriptionValidationError(
                "transcript segment text must be non-empty"
            )
        previous_end = numeric_end


def _validate_raw_timestamps(
    payload: Any,
    audio_id: str,
    variant_id: str,
    provider: str,
    language: str,
    duration: float,
) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise TranscriptionValidationError("unsupported raw_timestamps schema_version")
    expected = {
        "audio_id": audio_id,
        "variant_id": variant_id,
        "provider": provider,
        "language": language,
        "duration": duration,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise TranscriptionValidationError(
                f"raw_timestamps {field} does not match manifest"
            )
    items = payload.get("items")
    if not isinstance(items, list):
        raise TranscriptionValidationError("raw_timestamps items must be an array")
    previous_end = 0.0
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "text",
            "start",
            "end",
            "probability",
        }:
            raise TranscriptionValidationError(
                "raw_timestamps item has an invalid shape"
            )
        if not isinstance(item["text"], str) or not item["text"]:
            raise TranscriptionValidationError(
                "raw_timestamps item text must be non-empty"
            )
        start = item["start"]
        end = item["end"]
        if (
            not _finite_nonnegative(start)
            or not _finite_nonnegative(end)
            or start < previous_end
            or end < start
        ):
            raise TranscriptionValidationError(
                "raw_timestamps timestamps must be monotonic"
            )
        probability = item["probability"]
        if probability is not None and (
            not isinstance(probability, (int, float))
            or isinstance(probability, bool)
            or not math.isfinite(probability)
            or probability < 0
            or probability > 1
        ):
            raise TranscriptionValidationError("raw_timestamps probability is invalid")
        if provider == "qwen3" and probability is not None:
            raise TranscriptionValidationError(
                "Qwen3 raw_timestamps probability must remain null"
            )
        previous_end = end


def validate_transcription_manifest(
    manifest_path: Path, expected_audio_path: Path
) -> tuple[dict[str, Any], Path]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_absolute() or not manifest_path.is_file():
        raise TranscriptionValidationError(
            f"transcription manifest does not exist: {manifest_path}"
        )
    try:
        manifest = read_json(manifest_path)
    except Exception as exc:
        raise TranscriptionValidationError(
            f"cannot read transcription manifest: {exc}"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != SCHEMA_VERSION
    ):
        raise TranscriptionValidationError(
            "unsupported transcription manifest schema_version"
        )
    if manifest.get("status") != "complete":
        raise TranscriptionValidationError("transcription manifest is not complete")

    audio = manifest.get("audio")
    request = manifest.get("request")
    artifacts = manifest.get("artifacts")
    artifact_sha256 = manifest.get("artifact_sha256")
    if (
        not isinstance(audio, dict)
        or not isinstance(request, dict)
        or not isinstance(artifacts, dict)
        or not isinstance(artifact_sha256, dict)
    ):
        raise TranscriptionValidationError(
            "transcription manifest is missing required objects"
        )

    audio_id = audio.get("id")
    audio_size = audio.get("size")
    sample_count = audio.get("sample_count")
    sample_rate = audio.get("sample_rate")
    variant_id = request.get("variant_id")
    provider = request.get("provider")
    language = request.get("language")
    duration = audio.get("duration")
    if not isinstance(audio_id, str) or SHA256_PATTERN.fullmatch(audio_id) is None:
        raise TranscriptionValidationError("transcription manifest audio.id is invalid")
    if (
        not isinstance(audio_size, int)
        or isinstance(audio_size, bool)
        or audio_size < 0
    ):
        raise TranscriptionValidationError(
            "transcription manifest audio.size is invalid"
        )
    for field, value in (("sample_count", sample_count), ("sample_rate", sample_rate)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TranscriptionValidationError(
                f"transcription manifest audio.{field} is invalid"
            )
    if not isinstance(variant_id, str) or SHA256_PATTERN.fullmatch(variant_id) is None:
        raise TranscriptionValidationError(
            "transcription manifest request.variant_id is invalid"
        )
    try:
        canonical_request = {
            key: value for key, value in request.items() if key != "variant_id"
        }
        canonical_bytes = json.dumps(
            canonical_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TranscriptionValidationError(
            "transcription manifest request is not canonicalizable"
        ) from exc
    if hashlib.sha256(canonical_bytes).hexdigest() != variant_id:
        raise TranscriptionValidationError(
            "transcription manifest request.variant_id does not match request"
        )
    for field in (
        "provider_identity",
        "execution_policy",
        "vad_parameters",
        "planning_parameters",
    ):
        if not isinstance(request.get(field), dict):
            raise TranscriptionValidationError(
                f"transcription manifest request.{field} is invalid"
            )
    if request.get("segmentation_schema_version") != 1:
        raise TranscriptionValidationError(
            "transcription manifest segmentation schema is invalid"
        )
    if not isinstance(provider, str) or provider not in PUBLIC_PROVIDERS:
        raise TranscriptionValidationError(
            "transcription manifest request.provider is invalid"
        )
    if not isinstance(language, str) or not language:
        raise TranscriptionValidationError(
            "transcription manifest request.language is invalid"
        )
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not _finite_nonnegative(duration)
    ):
        raise TranscriptionValidationError(
            "transcription manifest audio.duration is invalid"
        )
    numeric_duration = float(duration)

    expected_audio_path = expected_audio_path.resolve()
    if not expected_audio_path.is_file():
        raise TranscriptionValidationError(
            f"job audio does not exist: {expected_audio_path}"
        )
    if expected_audio_path.stat().st_size != audio_size:
        raise TranscriptionValidationError(
            "transcription audio size does not match job audio"
        )
    if sha256_file(expected_audio_path) != audio_id:
        raise TranscriptionValidationError(
            "transcription audio hash does not match job audio"
        )

    resolved_artifacts: dict[str, Path] = {}
    log_path = resolve_manifest_artifact(manifest_path, artifacts, "log")
    workspace_path = resolve_manifest_artifact(manifest_path, artifacts, "workspace")
    if not log_path.is_file() or not workspace_path.is_dir():
        raise TranscriptionValidationError(
            "transcription manifest log or workspace is missing"
        )
    for name in ("transcript", "raw_timestamps"):
        artifact_path = resolve_manifest_artifact(manifest_path, artifacts, name)
        if not artifact_path.is_file():
            raise TranscriptionValidationError(
                f"transcription artifact does not exist: {name}"
            )
        expected_digest = artifact_sha256.get(name)
        if (
            not isinstance(expected_digest, str)
            or SHA256_PATTERN.fullmatch(expected_digest) is None
        ):
            raise TranscriptionValidationError(
                f"transcription artifact digest is invalid: {name}"
            )
        if sha256_file(artifact_path) != expected_digest:
            raise TranscriptionValidationError(
                f"transcription artifact digest mismatch: {name}"
            )
        resolved_artifacts[name] = artifact_path

    try:
        transcript = read_json(resolved_artifacts["transcript"])
    except Exception as exc:
        raise TranscriptionValidationError(f"cannot read transcript: {exc}") from exc
    try:
        raw_timestamps = read_json(resolved_artifacts["raw_timestamps"])
    except Exception as exc:
        raise TranscriptionValidationError(
            f"cannot read raw_timestamps: {exc}"
        ) from exc
    _validate_transcript(
        transcript,
        audio_id,
        variant_id,
        provider,
        language,
        numeric_duration,
    )
    _validate_raw_timestamps(
        raw_timestamps,
        audio_id,
        variant_id,
        provider,
        language,
        numeric_duration,
    )
    return manifest, resolved_artifacts["transcript"]


def invalidate_external_transcription(
    job_path: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    prompt_path: Path | None = None
    prompt = payload.get("prompt")
    if isinstance(prompt, dict):
        prompt_value = prompt.get("path")
        if isinstance(prompt_value, str):
            try:
                prompt_path = resolve_local_path(job_path, prompt_value, "prompt.path")
            except JobValidationError:
                prompt_path = None

    rolled_back = {
        **payload,
        "status": "needs_transcription",
        "transcript": None,
        "transcription_manifest": None,
        "prompt": None,
        "error": None,
    }
    publish_job(job_path, rolled_back)
    expected_prompt = job_path.resolve().parent / (
        f"{payload['video']['bvid']}_summary_prompt.md"
    )
    if prompt_path is not None and prompt_path == expected_prompt:
        try:
            prompt_path.unlink(missing_ok=True)
        except OSError:
            pass
    return rolled_back
