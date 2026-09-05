from __future__ import annotations

import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Generator

from audio_transcribe_contract import (
    PUBLIC_SCHEMA_VERSION,
    ResultManifest,
    ResultValidationError,
    load_manifest,
    load_result,
)

from scripts.asr.alignment import (
    AlignedTranscript,
    AlignmentContractError,
    AlignmentItem,
    project_normalized_text,
    validate_alignment,
)
from scripts.asr.segmentation import build_sentence_segments
from scripts.io_utils import (
    pretty_json_bytes,
    read_json,
    write_bytes_atomic,
    write_json_atomic,
)
from scripts.process_logging import get_logger
from scripts.text_normalization import normalize_transcript_text

logger = get_logger(__name__)


# @contextmanager 让这个函数可以配合 with 使用
@contextmanager
def result_lock(result_dir: Path) -> Generator[None, None, None]:
    """Hold a process lock while checking or publishing one audio/configuration result."""
    result_dir.mkdir(parents=True, exist_ok=True)
    lock_path = result_dir / ".result.lock"
    stream = lock_path.open("a+b")
    # 锁标记
    locked = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            # 为 Windows 准备一个可供锁定的字节
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            # Windows
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            # Unix
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        locked = True
        # 在 yield 之前设置排他锁
        yield  # 在此处执行 with 中的代码
        # 完成 with 中的代码后继续
    finally:
        # 只有在确认加锁才执行解锁代码
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _resolve_artifact_path(manifest_path: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ResultValidationError("Artifact path must be a non-empty string.")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ResultValidationError("Artifact path must be relative.")
    root = manifest_path.parent.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ResultValidationError(
            "Artifact path escapes the result directory."
        ) from exc
    return resolved


def write_workspace_result(
    workspace_path: Path,
    *,
    audio_id: str,
    config_digest: str,
    text: str,
    items: list[AlignmentItem],
    duration: float,
    provider: str,
    language: str,
) -> None:
    alignment = AlignedTranscript(text, tuple(items))
    if not text.strip() or not items:
        raise ResultValidationError(
            "A complete transcription must contain text and timestamps."
        )
    alignment = project_normalized_text(
        alignment,
        normalize_transcript_text(text, language),
        language=language,
    )
    validate_alignment(alignment, duration, language=language)
    write_json_atomic(
        workspace_path,
        {
            "audio_id": audio_id,
            "config_digest": config_digest,
            "provider": provider,
            "language": language,
            "duration": duration,
            "text": alignment.text,
            "items": [
                {
                    "text": item.text,
                    "start": item.start,
                    "end": item.end,
                    "probability": item.probability,
                }
                for item in alignment.items
            ],
        },
    )


def load_workspace_result(
    workspace_path: Path,
    *,
    expected_audio_id: Any,
    expected_config_digest: Any,
    expected_provider: Any,
    expected_language: Any,
    expected_duration: Any,
) -> tuple[AlignedTranscript, float, str, str]:
    try:
        workspace = read_json(workspace_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ResultValidationError("Invalid workspace result.") from None
    expected_keys = {
        "audio_id",
        "config_digest",
        "text",
        "items",
        "duration",
        "provider",
        "language",
    }
    if (
        not isinstance(workspace, dict)
        or set(workspace) != expected_keys
        or not isinstance(workspace["audio_id"], str)
        or not workspace["audio_id"]
        or workspace["audio_id"] != expected_audio_id
        or not isinstance(workspace["config_digest"], str)
        or not workspace["config_digest"]
        or workspace["config_digest"] != expected_config_digest
        or not isinstance(workspace["text"], str)
        or not workspace["text"].strip()
        or not isinstance(workspace["items"], list)
        or not workspace["items"]
        or isinstance(workspace["duration"], bool)
        or not isinstance(workspace["duration"], (int, float))
        or not math.isfinite(workspace["duration"])
        or workspace["duration"] <= 0
        or workspace["duration"] != expected_duration
        or not isinstance(workspace["provider"], str)
        or workspace["provider"] not in {"faster-whisper", "qwen3-asr"}
        or workspace["provider"] != expected_provider
        or not isinstance(workspace["language"], str)
        or not workspace["language"]
        or workspace["language"] != expected_language
    ):
        raise ResultValidationError("Invalid workspace result.")
    items: list[AlignmentItem] = []
    for raw_item in workspace["items"]:
        if (
            not isinstance(raw_item, dict)
            or set(raw_item) != {"text", "start", "end", "probability"}
            or not isinstance(raw_item["text"], str)
            or not raw_item["text"]
            or any(
                isinstance(raw_item[field], bool)
                or not isinstance(raw_item[field], (int, float))
                for field in ("start", "end")
            )
            or (
                raw_item["probability"] is not None
                and (
                    isinstance(raw_item["probability"], bool)
                    or not isinstance(raw_item["probability"], (int, float))
                )
            )
            or (
                workspace["provider"] == "qwen3-asr"
                and raw_item["probability"] is not None
            )
        ):
            raise ResultValidationError("Invalid workspace result.")
        items.append(
            AlignmentItem(
                raw_item["text"],
                raw_item["start"],
                raw_item["end"],
                raw_item["probability"],
            )
        )
    alignment = AlignedTranscript(workspace["text"], tuple(items))
    try:
        validate_alignment(
            alignment,
            workspace["duration"],
            language=workspace["language"],
        )
    except (AlignmentContractError, TypeError, ValueError):
        raise ResultValidationError("Invalid workspace result.") from None
    return (
        alignment,
        workspace["duration"],
        workspace["provider"],
        workspace["language"],
    )


def _public_payload(
    workspace_path: Path,
    *,
    audio_id: str,
    config_digest: str,
    provider: Any,
    language: Any,
    duration: Any,
) -> dict[str, Any]:
    alignment, duration, provider, language = load_workspace_result(
        workspace_path,
        expected_audio_id=audio_id,
        expected_config_digest=config_digest,
        expected_provider=provider,
        expected_language=language,
        expected_duration=duration,
    )
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "audio_id": audio_id,
        "config_digest": config_digest,
        "provider": provider,
        "language": language,
        "duration": duration,
        "segments": build_sentence_segments(alignment, language=language),
        "items": [
            {
                "text": item.text,
                "start": item.start,
                "end": item.end,
                "probability": item.probability,
            }
            for item in alignment.items
        ],
    }


def matching_manifest(
    manifest_path: Path, *, audio: dict[str, Any], request: dict[str, Any]
) -> ResultManifest | None:
    """Reject other identities and retain valid metadata for controlled repair."""
    try:
        manifest = load_manifest(manifest_path)
    except ResultValidationError:
        return None
    if manifest["audio"] != audio or manifest["request"] != request:
        raise ResultValidationError(
            "Published result identity does not match current request."
        )
    return manifest


def publish_result(
    result_dir: Path,
    *,
    audio: dict[str, Any],
    request: dict[str, Any],
    replace_existing: bool = False,
) -> Path:
    """Validate a staged bundle, then publish its body and manifest last.

    Damaged bundles may be republished under the same audio/request identity.
    Exact recovery preserves manifest bytes; republication updates only its digest.
    The caller holds result_lock across cache inspection, rebuild and publication.
    """
    manifest_path = result_dir / "manifest.json"
    if manifest_path.exists() and not replace_existing:
        raise ResultValidationError("A complete result manifest already exists.")
    previous = matching_manifest(manifest_path, audio=audio, request=request)
    if previous is not None:
        try:
            load_result(manifest_path)
        except ResultValidationError:
            pass
        else:
            return manifest_path
    transcript = _public_payload(
        result_dir / "workspace" / "result.json",
        audio_id=audio["id"],
        config_digest=request["config_digest"],
        provider=request.get("provider"),
        language=request.get("language"),
        duration=audio.get("duration"),
    )
    transcript_bytes = pretty_json_bytes(transcript) + b"\n"
    digest = hashlib.sha256(transcript_bytes).hexdigest()
    if previous is not None:
        if digest == previous["artifact_sha256"]["transcript"]:
            manifest_bytes = manifest_path.read_bytes()
        else:
            manifest_bytes = (
                pretty_json_bytes(
                    {**previous, "artifact_sha256": {"transcript": digest}}
                )
                + b"\n"
            )
        transcript_relative = previous["artifacts"]["transcript"]
    else:
        transcript_relative = "transcript.json"
        manifest_bytes = (
            pretty_json_bytes(
                {
                    "schema_version": PUBLIC_SCHEMA_VERSION,
                    "status": "complete",
                    "audio": audio,
                    "request": request,
                    "artifacts": {"transcript": transcript_relative},
                    "artifact_sha256": {"transcript": digest},
                }
            )
            + b"\n"
        )
    transcript_path = _resolve_artifact_path(manifest_path, transcript_relative)
    # Stage both files under their final relative names so the public loader
    # validates exactly the bytes and paths that will be published.
    with TemporaryDirectory(prefix=".publication-", dir=result_dir) as temporary:
        staging = Path(temporary)
        staged_transcript = _resolve_artifact_path(
            staging / "manifest.json", transcript_relative
        )
        write_bytes_atomic(staged_transcript, transcript_bytes)
        staged_manifest = staging / "manifest.json"
        write_bytes_atomic(staged_manifest, manifest_bytes)
        load_result(staged_manifest)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        original_transcript = (
            transcript_path.read_bytes() if transcript_path.is_file() else None
        )
        os.replace(staged_transcript, transcript_path)
        try:
            os.replace(staged_manifest, manifest_path)
        except OSError:
            # A failed final install must not leave a replacement body behind.
            if original_transcript is None:
                transcript_path.unlink()
            else:
                write_bytes_atomic(transcript_path, original_transcript)
            raise
    if previous is None:
        logger.info(
            "Publication complete: audio_id=%s config_digest=%s digest=%s",
            audio["id"],
            request["config_digest"],
            digest,
        )
    elif digest == previous["artifact_sha256"]["transcript"]:
        logger.info(
            "Exact recovery complete: audio_id=%s config_digest=%s digest=%s",
            audio["id"],
            request["config_digest"],
            digest,
        )
    else:
        logger.warning(
            "Republication complete: audio_id=%s config_digest=%s old_digest=%s new_digest=%s",
            audio["id"],
            request["config_digest"],
            previous["artifact_sha256"]["transcript"],
            digest,
        )
    return manifest_path
