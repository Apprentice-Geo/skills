from __future__ import annotations

import os
import unicodedata
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Generator

from audio_transcribe_contract import ResultValidationError, load_result

from scripts.alignment import AlignmentItem, build_sentence_segments, validate_alignment
from scripts.io_utils import (
    read_json,
    sha256_file,
    write_json_atomic,
)
from scripts.text_normalization import normalize_transcript_text

PUBLIC_SCHEMA_VERSION = 1
WORKSPACE_SCHEMA_VERSION = 1


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


def _project_normalized_items(
    source: str, normalized: str, items: list[AlignmentItem]
) -> list[AlignmentItem]:
    owners: list[int | None] = [None] * len(source)
    source_index = 0
    for item_index, item in enumerate(items):
        for char in item.text:
            while source[source_index] != char:
                source_index += 1
            owners[source_index] = item_index
            source_index += 1

    fragments: list[tuple[str, object, float, float, float | None]] = []

    def append(text: str, owner_indexes: list[int]) -> None:
        if not text or not owner_indexes:
            return
        unique_owners = list(dict.fromkeys(owner_indexes))
        owner_items = [items[index] for index in unique_owners]
        probabilities = [
            item.probability for item in owner_items if item.probability is not None
        ]
        key: object = (
            ("item", unique_owners[0])
            if len(unique_owners) == 1
            else ("group", tuple(unique_owners))
        )
        fragment = (
            text,
            key,
            owner_items[0].start,
            owner_items[-1].end,
            min(probabilities) if probabilities else None,
        )
        if fragments and fragments[-1][1:] == fragment[1:]:
            previous = fragments[-1]
            fragments[-1] = (previous[0] + text, *previous[1:])
        else:
            fragments.append(fragment)

    def adjacent_owner(position: int) -> int | None:
        return next(
            (owner for owner in reversed(owners[:position]) if owner is not None),
            next((owner for owner in owners[position:] if owner is not None), None),
        )

    for tag, source_start, source_end, normalized_start, normalized_end in (
        SequenceMatcher(None, source, normalized, autojunk=False).get_opcodes()
    ):
        replacement = normalized[normalized_start:normalized_end]
        if tag == "equal" or (
            tag == "replace"
            and source_end - source_start == normalized_end - normalized_start
        ):
            for offset, char in enumerate(replacement):
                owner = owners[source_start + offset]
                if owner is not None:
                    append(char, [owner])
        elif tag == "replace":
            affected = [
                owner
                for owner in owners[source_start:source_end]
                if owner is not None
            ]
            if affected:
                append(replacement, affected)
            else:
                owner = adjacent_owner(source_start)
                if owner is not None:
                    append(
                        "".join(
                            char
                            for char in replacement
                            if not (
                                char.isspace()
                                or unicodedata.category(char).startswith("P")
                            )
                        ),
                        [owner],
                    )
        elif tag == "insert":
            owner = adjacent_owner(source_start)
            if owner is not None:
                for char in replacement:
                    if not (
                        char.isspace()
                        or unicodedata.category(char).startswith("P")
                    ):
                        append(char, [owner])

    return [
        AlignmentItem(text, start, end, probability)
        for text, _, start, end, probability in fragments
    ]


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
        raise ResultValidationError(
            "A complete transcription must contain text and timestamps."
        )
    normalized_text = normalize_transcript_text(text, language)
    items = _project_normalized_items(text, normalized_text, items)
    text = normalized_text
    validate_alignment(text, items, duration)
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
        raise ResultValidationError("Invalid workspace result.")
    raw_items = workspace.get("items")
    try:
        if not isinstance(raw_items, list):
            raise TypeError("timestamp items must be an array")
        items = [AlignmentItem(**item) for item in raw_items]
        duration = float(workspace["duration"])
        provider = str(workspace["provider"])
        language = str(workspace["language"])
        text = str(workspace["text"])
    except (KeyError, TypeError, ValueError):
        raise ResultValidationError("Invalid workspace result.") from None
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
                "start": min(item.start, duration),
                "end": min(item.end, duration),
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
    variant_id = request["variant_id"]
    workspace_path = _resolve_artifact_path(manifest_path, workspace_relative)
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
    load_result(manifest_path)
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
            _resolve_artifact_path(manifest_path, original["artifacts"]["workspace"])
            / "result.json"
        )
        transcript, raw = _public_payloads(
            workspace_path,
            audio_id=audio["id"],
            variant_id=request["variant_id"],
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
