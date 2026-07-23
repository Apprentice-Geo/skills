from pathlib import Path
from typing import Any

from scripts.utils import ensure_dir, read_json


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
    segments = payload["segments"]
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
