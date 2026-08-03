import math
import unicodedata
from pathlib import Path
from typing import Any

from scripts.utils import read_json, write_text_atomic

STRONG_ENDINGS = frozenset("。！？.!?")


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def write_markdown_from_json(
    json_path: Path,
    markdown_path: Path,
) -> None:
    payload = read_json(json_path)
    write_markdown(markdown_path, payload)


def _merged_segments(segments: Any) -> list[tuple[float, float, str]]:
    if not isinstance(segments, list) or not segments:
        raise ValueError("transcript segments must be a non-empty array")

    merged: list[tuple[float, float, str]] = []
    current_start = current_end = 0.0
    current_text = ""
    previous_end = 0.0
    for expected_id, segment in enumerate(segments):
        if not isinstance(segment, dict) or segment.get("id") != expected_id:
            raise ValueError("transcript segment ids must be continuous")
        start = segment.get("start")
        end = segment.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < previous_end
            or end <= start
        ):
            raise ValueError("transcript segment timestamps are invalid")
        text = segment.get("text")
        if not isinstance(text, str) or not (text := text.strip()):
            raise ValueError("transcript segment text must be non-empty")

        if current_text and start - current_end > 5:
            merged.append((current_start, current_end, current_text))
            current_text = ""
        if not current_text:
            current_start = float(start)
            current_text = text
        else:
            separator = (
                "" if unicodedata.category(current_text[-1]).startswith("P") else " "
            )
            current_text += separator + text
        current_end = float(end)
        previous_end = current_end
        if current_text[-1] in STRONG_ENDINGS or len(current_text) > 64:
            merged.append((current_start, current_end, current_text))
            current_text = ""

    if current_text:
        merged.append((current_start, current_end, current_text))
    return merged


def render_markdown(payload: dict[str, Any]) -> str:
    segments = _merged_segments(payload.get("segments"))

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

    for start_seconds, end_seconds, text in segments:
        start = format_timestamp(start_seconds)
        end = format_timestamp(end_seconds)
        lines.append(f"[{start} - {end}] {text}")

    return "\n".join(lines).rstrip() + "\n"


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    write_text_atomic(path, render_markdown(payload))
