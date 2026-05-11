import argparse
import re
from pathlib import Path
from typing import Any

from manifest_io import (
    infer_result_dir,
    load_manifest,
    load_metadata_from_manifest,
    resolve_manifest_path,
    resolve_path,
)
from subtitle_utils import infer_subtitle_language
from transcript_output import write_markdown
from utils import ensure_dir, path_to_posix, write_json


SRT_TIME_PATTERN = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)


def parse_srt_timestamp(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000.0
    )


def normalize_text(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(line.strip() for line in lines if line.strip())).strip()


def parse_srt(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"\r?\n\r?\n+", path.read_text(encoding="utf-8-sig").strip())
    segments: list[dict[str, Any]] = []

    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue

        time_index = 1 if lines[0].isdigit() and len(lines) >= 3 else 0
        match = SRT_TIME_PATTERN.fullmatch(lines[time_index].strip())
        if not match:
            continue

        text = normalize_text(lines[time_index + 1 :])
        if not text:
            continue

        segments.append(
            {
                "id": len(segments),
                "start": round(parse_srt_timestamp(match.group("start")), 3),
                "end": round(parse_srt_timestamp(match.group("end")), 3),
                "text": text,
            }
        )

    return segments


def probe_srt(path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        segments = parse_srt(path)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if not segments:
        return None, "Subtitle SRT is empty or invalid."

    return segments, None


def subtitle_to_transcript(
    subtitle_path: Path,
    manifest: dict[str, Any],
    metadata: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    suffix = subtitle_path.suffix.lower()
    if suffix != ".srt":
        raise ValueError(f"Unsupported subtitle format: {subtitle_path}")

    segments = parse_srt(subtitle_path)
    video_id = manifest.get("id") or subtitle_path.stem
    output_stem = f"{video_id}_transcript"
    json_path = output_dir / f"{output_stem}.json"
    md_path = output_dir / f"{output_stem}.md"
    subtitle_language = infer_subtitle_language(subtitle_path)

    payload = {
        "bvid": video_id,
        "title": manifest.get("title"),
        "url": manifest.get("url"),
        "uploader": metadata.get("uploader"),
        "duration_string": metadata.get("duration_string"),
        "source": "subtitle",
        "language": subtitle_language,
        "subtitle_path": path_to_posix(subtitle_path),
        "segments": segments,
    }

    write_json(json_path, payload)
    write_markdown(md_path, payload)

    print(f"Subtitle: {path_to_posix(subtitle_path)}")
    print(f"JSON: {path_to_posix(json_path)}")
    print(f"Markdown: {path_to_posix(md_path)}")
    print(f"Segments: {len(segments)}")
    return {
        "subtitle_path": subtitle_path,
        "json_path": json_path,
        "markdown_path": md_path,
        "segments": segments,
        "payload": payload,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a subtitle file into the unified transcript outputs.")
    parser.add_argument("subtitle", help="Path to a subtitle file.")
    parser.add_argument("--manifest", type=Path, required=True, help="Path to resource/fetch_manifest.json.")
    parser.add_argument("--output-dir", type=Path, help="Result directory for transcript outputs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    subtitle_path = resolve_path(args.subtitle)
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_manifest(manifest_path)
    metadata = load_metadata_from_manifest(manifest)
    output_dir = infer_result_dir(manifest_path, subtitle_path, args.output_dir)
    ensure_dir(output_dir)
    subtitle_to_transcript(subtitle_path, manifest, metadata, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
