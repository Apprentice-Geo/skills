from __future__ import annotations

import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from audio_transcribe_contract import ResultValidationError, load_result

from scripts.asr.alignment import (
    AlignedTranscript,
    AlignmentContractError,
    AlignmentItem,
    project_normalized_text,
    validate_alignment,
)
from scripts.asr.segmentation import build_sentence_segments
from scripts.io_utils import (
    read_json,
    sha256_file,
    write_json_atomic,
)
from scripts.text_normalization import normalize_transcript_text

PUBLIC_SCHEMA_VERSION = 1


# @contextmanager 让这个函数可以配合 with 使用
@contextmanager
def variant_lock(variant_dir: Path) -> Generator[None, None, None]:
    """Hold a process lock while checking or publishing one result variant."""
    variant_dir.mkdir(parents=True, exist_ok=True)
    lock_path = variant_dir / ".variant.lock"
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
    variant_id: str,
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
            "variant_id": variant_id,
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
    expected_variant_id: Any,
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
        "variant_id",
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
        or not isinstance(workspace["variant_id"], str)
        or not workspace["variant_id"]
        or workspace["variant_id"] != expected_variant_id
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


def _public_payloads(
    workspace_path: Path,
    *,
    audio_id: str,
    variant_id: str,
    provider: Any,
    language: Any,
    duration: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    alignment, duration, provider, language = load_workspace_result(
        workspace_path,
        expected_audio_id=audio_id,
        expected_variant_id=variant_id,
        expected_provider=provider,
        expected_language=language,
        expected_duration=duration,
    )
    items = list(alignment.items)
    segments = build_sentence_segments(alignment, language=language)
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
    return transcript, raw


def publish_result(
    variant_dir: Path,
    *,
    audio: dict[str, Any],
    request: dict[str, Any],
    workspace_relative: str = "workspace/result.json",
) -> Path:
    manifest_path = variant_dir / "result_manifest.json"
    if manifest_path.exists():
        raise ResultValidationError("A complete result manifest already exists.")
    variant_id = request["variant_id"]
    workspace_path = _resolve_artifact_path(manifest_path, workspace_relative)
    transcript, raw = _public_payloads(
        workspace_path,
        audio_id=audio["id"],
        variant_id=variant_id,
        provider=request.get("provider"),
        language=request.get("language"),
        duration=audio.get("duration"),
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
    candidate_path = variant_dir / ".result_manifest.json.incomplete"
    try:
        write_json_atomic(candidate_path, manifest)
        load_result(candidate_path)
        os.replace(candidate_path, manifest_path)
    except Exception:
        candidate_path.unlink(missing_ok=True)
        raise
    return manifest_path


def recover_public_artifacts(manifest_path: Path) -> dict[str, Any]:
    """Repair public files only when workspace bytes reproduce published digests."""
    original_bytes = manifest_path.read_bytes()
    hidden_path = manifest_path.with_name(f".{manifest_path.name}.recovery")
    os.replace(manifest_path, hidden_path)
    try:
        original = read_json(hidden_path)
        audio = original["audio"]
        request = original["request"]
        workspace_path = (
            _resolve_artifact_path(manifest_path, original["artifacts"]["workspace"])
            / "result.json"
        )
        transcript, raw = _public_payloads(
            workspace_path,
            audio_id=audio["id"],
            variant_id=request["variant_id"],
            provider=request.get("provider"),
            language=request.get("language"),
            duration=audio.get("duration"),
        )
        transcript_path = _resolve_artifact_path(
            manifest_path, original["artifacts"]["transcript"]
        )
        raw_path = _resolve_artifact_path(
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
            raise ResultValidationError(
                "Workspace reconstruction does not match published artifact digests."
            )
        # Restore the immutable manifest byte-for-byte after deterministic repair.
        from scripts.io_utils import write_bytes_atomic

        write_bytes_atomic(manifest_path, original_bytes)
        hidden_path.unlink(missing_ok=True)
        return dict(load_result(manifest_path).manifest)
    except Exception:
        # Keep the last complete manifest available if reconstruction fails.
        from scripts.io_utils import write_bytes_atomic

        write_bytes_atomic(manifest_path, original_bytes)
        hidden_path.unlink(missing_ok=True)
        raise
