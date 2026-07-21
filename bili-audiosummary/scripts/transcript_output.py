from pathlib import Path
from typing import Any

from scripts.asr.common import is_chinese_language
from scripts.utils import ensure_dir, read_json

STRONG_PUNCTUATION = set("。.!！？?")
WEAK_PUNCTUATION = set("，,；;")
MIN_WEAK_PUNCTUATION_SECONDS = 3.0
TARGET_SEGMENT_SECONDS = 10.0
MAX_SEGMENT_CHARACTERS = 50
SILENCE_GAP_SECONDS = 1.5


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _non_whitespace_length(text: str) -> int:
    return sum(not char.isspace() for char in text)


def normalize_segments_for_markdown(
    segments: list[dict[str, Any]],
    language: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    separator = "，" if is_chinese_language(language) else ", "

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        current["id"] = len(normalized)
        normalized.append(current)
        current = None

    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue

        start = float(segment["start"])
        end = float(segment["end"])
        if current is not None and start - float(current["end"]) >= SILENCE_GAP_SECONDS:
            flush()

        if current is None:
            current = {
                "id": 0,
                "start": start,
                "end": end,
                "text": text,
            }
        else:
            current["end"] = end
            current["text"] = separator.join(
                part for part in (str(current["text"]).strip(), text) if part
            )

        duration = float(current["end"]) - float(current["start"])
        last_character = str(current["text"])[-1]
        if (
            last_character in STRONG_PUNCTUATION
            or (
                last_character in WEAK_PUNCTUATION
                and duration >= MIN_WEAK_PUNCTUATION_SECONDS
            )
            or duration >= TARGET_SEGMENT_SECONDS
            or _non_whitespace_length(str(current["text"])) >= MAX_SEGMENT_CHARACTERS
        ):
            flush()

    flush()
    return normalized


def write_markdown_from_json(
    json_path: Path,
    markdown_path: Path,
    *,
    normalize_segments: bool,
) -> None:
    payload = read_json(json_path)
    segments = payload["segments"]
    if normalize_segments:
        segments = normalize_segments_for_markdown(
            segments,
            str(payload.get("language") or ""),
        )

    _write_markdown(markdown_path, payload, segments)


def _write_markdown(
    path: Path,
    payload: dict[str, Any],
    segments: list[dict[str, Any]],
) -> None:
    ensure_dir(path.parent)

    lines = [
        "## metadata",
        "",
        f"title: {payload.get('title') or ''}",
        f"bvid: {payload.get('bvid') or ''}",
        f"url: {payload.get('url') or ''}",
        f"uploader: {payload.get('uploader') or ''}",
        f"duration: {payload.get('duration_string') or payload.get('duration') or ''}",
        f"source: {payload.get('source') or ''}",
        f"language: {payload.get('language') or ''}",
        "",
        "## transcript text",
        "",
    ]

    for segment in segments:
        start = format_timestamp(segment["start"])
        end = format_timestamp(segment["end"])
        text = segment["text"]
        if text:
            lines.append(f"[{start} - {end}] {text}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
