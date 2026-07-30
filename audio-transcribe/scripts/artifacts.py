from __future__ import annotations

import hashlib
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from scripts.alignment import AlignmentItem, build_sentence_segments, validate_alignment
from scripts.io_utils import (
    canonical_json_bytes,
    read_json,
    sha256_file,
    write_json_atomic,
)

PUBLIC_SCHEMA_VERSION = 1
WORKSPACE_SCHEMA_VERSION = 1


class ArtifactContractError(ValueError):
    """A public artifact is unsafe, inconsistent, or structurally invalid."""


@contextmanager
def variant_lock(variant_dir: Path) -> Iterator[None]:
    """Hold a process lock while checking or publishing one result variant."""
    variant_dir.mkdir(parents=True, exist_ok=True)
    lock_path = variant_dir / ".variant.lock"
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


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactContractError(f"{field} must be a number.")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ArtifactContractError(f"{field} must be finite and non-negative.")
    return parsed


def _validate_common(payload: Any, *, audio_id: str, variant_id: str) -> float:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ArtifactContractError("Invalid public artifact schema_version.")
    if payload.get("audio_id") != audio_id or payload.get("variant_id") != variant_id:
        raise ArtifactContractError("Public artifact identity mismatch.")
    if payload.get("provider") not in {"faster-whisper", "qwen3"}:
        raise ArtifactContractError("Invalid public provider.")
    if not isinstance(payload.get("language"), str) or not payload["language"]:
        raise ArtifactContractError("Invalid public language.")
    return _finite_nonnegative(payload.get("duration"), "duration")


def validate_transcript(
    payload: Any, *, audio_id: str, variant_id: str
) -> dict[str, Any]:
    duration = _validate_common(payload, audio_id=audio_id, variant_id=variant_id)
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ArtifactContractError("Transcript segments must be non-empty.")
    previous_end = 0.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("id") != index:
            raise ArtifactContractError("Transcript segment ids must be continuous.")
        text = segment.get("text")
        if not isinstance(text, str) or not text:
            raise ArtifactContractError("Transcript segment text must be non-empty.")
        start = _finite_nonnegative(segment.get("start"), "segment.start")
        end = _finite_nonnegative(segment.get("end"), "segment.end")
        if start < previous_end or end <= start or end > duration:
            raise ArtifactContractError(
                "Transcript segment times must satisfy 0 <= start < end <= duration."
            )
        previous_end = end
    return payload


def validate_raw_timestamps(
    payload: Any, *, audio_id: str, variant_id: str
) -> dict[str, Any]:
    _validate_common(payload, audio_id=audio_id, variant_id=variant_id)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ArtifactContractError("Timestamp items must be an array.")
    previous_end = 0.0
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "text",
            "start",
            "end",
            "probability",
        }:
            raise ArtifactContractError("Invalid timestamp item shape.")
        if not isinstance(item["text"], str) or not item["text"]:
            raise ArtifactContractError("Timestamp item text must be non-empty.")
        start = _finite_nonnegative(item["start"], "item.start")
        end = _finite_nonnegative(item["end"], "item.end")
        probability = item["probability"]
        if probability is not None:
            probability = _finite_nonnegative(probability, "item.probability")
            if probability > 1:
                raise ArtifactContractError("item.probability must not exceed one.")
        if start < previous_end or end < start:
            raise ArtifactContractError("Timestamp item times must be monotonic.")
        if payload["provider"] == "qwen3" and probability is not None:
            raise ArtifactContractError("Qwen3 timestamp probability must remain null.")
        previous_end = end
    return payload


def resolve_artifact_path(manifest_path: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ArtifactContractError("Artifact path must be a non-empty string.")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ArtifactContractError("Artifact path must be relative.")
    root = manifest_path.parent.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ArtifactContractError(
            "Artifact path escapes the result directory."
        ) from exc
    return resolved


def validate_manifest(manifest_path: Path) -> dict[str, Any]:
    payload = read_json(manifest_path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != PUBLIC_SCHEMA_VERSION
        or payload.get("status") != "complete"
    ):
        raise ArtifactContractError("Invalid complete result manifest.")
    audio = payload.get("audio")
    request = payload.get("request")
    artifacts = payload.get("artifacts")
    digests = payload.get("artifact_sha256")
    if (
        not isinstance(audio, dict)
        or not isinstance(request, dict)
        or not isinstance(artifacts, dict)
        or not isinstance(digests, dict)
    ):
        raise ArtifactContractError("Invalid result manifest shape.")
    audio_id = audio.get("id")
    variant_id = request.get("variant_id")
    if (
        not isinstance(audio_id, str)
        or len(audio_id) != 64
        or not isinstance(variant_id, str)
        or len(variant_id) != 64
    ):
        raise ArtifactContractError("Invalid result identity.")
    canonical_request = {
        key: value for key, value in request.items() if key != "variant_id"
    }
    if (
        hashlib.sha256(canonical_json_bytes(canonical_request)).hexdigest()
        != variant_id
    ):
        raise ArtifactContractError("Result variant_id does not match request.")
    _finite_nonnegative(audio.get("size"), "audio.size")
    _finite_nonnegative(audio.get("sample_count"), "audio.sample_count")
    _finite_nonnegative(audio.get("sample_rate"), "audio.sample_rate")
    _finite_nonnegative(audio.get("duration"), "audio.duration")

    transcript_path = resolve_artifact_path(manifest_path, artifacts.get("transcript"))
    raw_path = resolve_artifact_path(manifest_path, artifacts.get("raw_timestamps"))
    log_path = resolve_artifact_path(manifest_path, artifacts.get("log"))
    workspace_path = resolve_artifact_path(manifest_path, artifacts.get("workspace"))
    if not log_path.is_file() or not workspace_path.is_dir():
        raise ArtifactContractError("Result log or workspace is missing.")
    for key, path in (("transcript", transcript_path), ("raw_timestamps", raw_path)):
        expected = digests.get(key)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ArtifactContractError(f"Invalid {key} digest.")
        if not path.is_file() or sha256_file(path) != expected:
            raise ArtifactContractError(f"{key} artifact digest mismatch.")
    transcript = validate_transcript(
        read_json(transcript_path), audio_id=audio_id, variant_id=variant_id
    )
    raw = validate_raw_timestamps(
        read_json(raw_path), audio_id=audio_id, variant_id=variant_id
    )
    for name, artifact in (("transcript", transcript), ("raw_timestamps", raw)):
        if (
            artifact.get("provider") != request.get("provider")
            or artifact.get("language") != request.get("language")
            or artifact.get("duration") != audio.get("duration")
        ):
            raise ArtifactContractError(f"{name} identity does not match manifest.")
    return payload


def write_workspace_result(
    workspace_path: Path,
    *,
    text: str,
    items: list[AlignmentItem],
    duration: float,
    provider: str,
    language: str,
) -> None:
    validate_alignment(text, items, duration)
    if not text.strip() or not items:
        raise ArtifactContractError(
            "A complete transcription must contain text and timestamps."
        )
    write_json_atomic(
        workspace_path,
        {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "text": text,
            "items": [
                {
                    "text": item.text,
                    "start": item.start,
                    "end": item.end,
                    "probability": item.probability,
                }
                for item in items
            ],
            "duration": duration,
            "provider": provider,
            "language": language,
        },
    )


def _public_payloads(
    workspace_path: Path, *, audio_id: str, variant_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = read_json(workspace_path)
    if (
        not isinstance(workspace, dict)
        or workspace.get("schema_version") != WORKSPACE_SCHEMA_VERSION
    ):
        raise ArtifactContractError("Invalid workspace result.")
    raw_items = workspace.get("items", workspace.get("words"))
    try:
        if not isinstance(raw_items, list):
            raise TypeError("timestamp items must be an array")
        items = [AlignmentItem(**item) for item in raw_items]
        duration = float(workspace["duration"])
        provider = str(workspace["provider"])
        language = str(workspace["language"])
        text = str(workspace["text"])
    except (KeyError, TypeError, ValueError):
        # The migrated ASR core stores identity and duration in its plan.
        try:
            plan = workspace["plan"]
            items = [AlignmentItem(**item) for item in workspace["words"]]
            duration = float(plan["source"]["sample_count"]) / float(
                plan["source"]["sample_rate"]
            )
            provider = str(plan["provider_request"]["provider"])
            language = str(plan["provider_request"]["language"])
            text = str(workspace["text"])
        except (KeyError, TypeError, ValueError) as nested:
            raise ArtifactContractError(
                f"Invalid workspace result: {nested}"
            ) from nested
    segments = build_sentence_segments(text, items, duration)
    transcript = {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "audio_id": audio_id,
        "variant_id": variant_id,
        "provider": provider,
        "language": language,
        "duration": duration,
        "segments": segments,
    }
    raw = {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "audio_id": audio_id,
        "variant_id": variant_id,
        "provider": provider,
        "language": language,
        "duration": duration,
        "items": [
            {
                "text": item.text,
                "start": item.start,
                "end": item.end,
                "probability": item.probability,
            }
            for item in items
        ],
    }
    validate_transcript(transcript, audio_id=audio_id, variant_id=variant_id)
    validate_raw_timestamps(raw, audio_id=audio_id, variant_id=variant_id)
    return transcript, raw


def publish_result(
    variant_dir: Path,
    *,
    audio: dict[str, Any],
    request: dict[str, Any],
    workspace_relative: str = "workspace/result.json",
) -> Path:
    manifest_path = variant_dir / "result_manifest.json"
    variant_id = request["variant_id"]
    workspace_path = resolve_artifact_path(manifest_path, workspace_relative)
    transcript, raw = _public_payloads(
        workspace_path, audio_id=audio["id"], variant_id=variant_id
    )
    transcript_path = variant_dir / "transcript.json"
    raw_path = variant_dir / "raw_timestamps.json"
    write_json_atomic(transcript_path, transcript)
    write_json_atomic(raw_path, raw)
    manifest = {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "status": "complete",
        "audio": audio,
        "request": request,
        "artifacts": {
            "transcript": "transcript.json",
            "raw_timestamps": "raw_timestamps.json",
            "log": "transcribe.log",
            "workspace": "workspace",
        },
        "artifact_sha256": {
            "transcript": sha256_file(transcript_path),
            "raw_timestamps": sha256_file(raw_path),
        },
    }
    # The manifest is the success entry and is therefore published last.
    write_json_atomic(manifest_path, manifest)
    validate_manifest(manifest_path)
    return manifest_path


def recover_public_artifacts(manifest_path: Path) -> dict[str, Any]:
    """Repair public files only when workspace bytes reproduce published digests."""
    original_bytes = manifest_path.read_bytes()
    hidden_path = manifest_path.with_name(f".{manifest_path.name}.incomplete")
    os.replace(manifest_path, hidden_path)
    try:
        original = read_json(hidden_path)
        audio = original["audio"]
        request = original["request"]
        workspace_path = (
            resolve_artifact_path(manifest_path, original["artifacts"]["workspace"])
            / "result.json"
        )
        transcript, raw = _public_payloads(
            workspace_path,
            audio_id=audio["id"],
            variant_id=request["variant_id"],
        )
        transcript_path = resolve_artifact_path(
            manifest_path, original["artifacts"]["transcript"]
        )
        raw_path = resolve_artifact_path(
            manifest_path, original["artifacts"]["raw_timestamps"]
        )
        write_json_atomic(transcript_path, transcript)
        write_json_atomic(raw_path, raw)
        expected = original["artifact_sha256"]
        actual = {
            "transcript": sha256_file(transcript_path),
            "raw_timestamps": sha256_file(raw_path),
        }
        if actual != expected:
            raise ArtifactContractError(
                "Workspace reconstruction does not match published artifact digests."
            )
        # Restore the immutable manifest byte-for-byte after deterministic repair.
        from scripts.io_utils import write_bytes_atomic

        write_bytes_atomic(manifest_path, original_bytes)
        hidden_path.unlink(missing_ok=True)
        return validate_manifest(manifest_path)
    except Exception:
        # No complete entry remains after failed recovery.
        manifest_path.unlink(missing_ok=True)
        raise


def canonical_artifact_bytes(payload: Any) -> bytes:
    """Expose artifact serialization for digest-oriented tests and integrations."""
    return canonical_json_bytes(payload) + b"\n"
