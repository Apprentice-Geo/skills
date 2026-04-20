import argparse
from pathlib import Path
from typing import Any, Optional

from faster_whisper import WhisperModel

from config import (
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_DEVICE,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    DEFAULT_WHISPER_MODEL_DIR,
    SKILL_ROOT,
)
from utils import ensure_dir, path_to_posix, read_json, write_json


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return SKILL_ROOT / path


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def default_model_path() -> str:
    if (DEFAULT_WHISPER_MODEL_DIR / "model.bin").exists():
        return path_to_posix(DEFAULT_WHISPER_MODEL_DIR)
    return "small"


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest must be a JSON object: {path}")
    return manifest


def load_metadata_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata_path = manifest.get("metadata_path")
    if not metadata_path:
        return {}

    path = resolve_path(str(metadata_path))
    if not path.exists():
        return {}

    metadata = read_json(path)
    if not isinstance(metadata, dict):
        return {}
    return metadata


def first_audio_from_manifest(manifest: dict[str, Any]) -> Path:
    audio_files = manifest.get("audio_files") or []
    if not audio_files:
        raise ValueError("Manifest does not contain audio_files. Run fetch_audio.py first.")
    return resolve_path(str(audio_files[0]))


def infer_result_dir(manifest_path: Optional[Path], audio_path: Path, output_dir: Optional[Path]) -> Path:
    if output_dir:
        return output_dir

    if manifest_path:
        return manifest_path.parent.parent

    return audio_path.parent.parent.parent


def make_segment(segment: Any, include_words: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": segment.id,
        "start": round(float(segment.start), 3),
        "end": round(float(segment.end), 3),
        "text": segment.text.strip(),
    }

    if include_words and segment.words:
        words = []
        for word in segment.words:
            word_data = {
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                "word": word.word,
            }
            if word.probability is not None:
                word_data["probability"] = round(float(word.probability), 4)
            words.append(word_data)
        data["words"] = words

    return data


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
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

    for segment in payload["segments"]:
        start = format_timestamp(segment["start"])
        end = format_timestamp(segment["end"])
        text = segment["text"]
        if text:
            lines.append(f"[{start} - {end}] {text}")
            lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def transcribe_audio(audio_path: Path, args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model_path = args.model or default_model_path()
    model = WhisperModel(
        model_path,
        device=args.device,
        compute_type=args.compute_type,
        cpu_threads=args.cpu_threads,
        num_workers=args.num_workers,
    )

    segments, info = model.transcribe(
        path_to_posix(audio_path),
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=args.vad_filter,
        word_timestamps=args.word_timestamps,
    )

    segment_list = [make_segment(segment, args.word_timestamps) for segment in segments]
    info_data = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "duration_after_vad": getattr(info, "duration_after_vad", None),
        "model": model_path,
        "device": args.device,
        "compute_type": args.compute_type,
        "vad_filter": args.vad_filter,
        "word_timestamps": args.word_timestamps,
    }
    return info_data, segment_list


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a fetched Bilibili audio file with faster-whisper."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to fetch_manifest.json or an audio file. Defaults to --manifest/--audio if provided.",
    )
    parser.add_argument("--manifest", type=Path, help="Path to resource/fetch_manifest.json.")
    parser.add_argument("--audio", type=Path, help="Path to an audio file.")
    parser.add_argument("--output-dir", type=Path, help="Result directory for transcript outputs.")
    parser.add_argument("--model", help="Model name or local faster-whisper model directory.")
    parser.add_argument("--language", default=DEFAULT_TRANSCRIBE_LANGUAGE)
    parser.add_argument("--device", default=DEFAULT_TRANSCRIBE_DEVICE)
    parser.add_argument("--compute-type", default=DEFAULT_TRANSCRIBE_COMPUTE_TYPE)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--cpu-threads", type=int, default=0)
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--vad-filter", dest="vad_filter", action="store_true", default=True)
    parser.add_argument("--no-vad-filter", dest="vad_filter", action="store_false")
    parser.add_argument("--word-timestamps", action="store_true")
    return parser.parse_args()


def resolve_inputs(args: argparse.Namespace) -> tuple[Optional[Path], Path, dict[str, Any]]:
    manifest_path = args.manifest
    audio_path = args.audio

    if args.input:
        input_path = resolve_path(args.input)
        if input_path.suffix.lower() == ".json":
            manifest_path = input_path
        else:
            audio_path = input_path

    manifest: dict[str, Any] = {}
    if manifest_path:
        manifest_path = resolve_path(path_to_posix(manifest_path))
        manifest = load_manifest(manifest_path)

    if audio_path:
        audio_path = resolve_path(path_to_posix(audio_path))
    elif manifest:
        audio_path = first_audio_from_manifest(manifest)
    else:
        raise ValueError("Provide a fetch manifest or an audio file.")

    return manifest_path, audio_path, manifest


def main() -> int:
    args = parse_args()
    run_transcribe(args)
    return 0


def run_transcribe(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path, audio_path, manifest = resolve_inputs(args)
    metadata = load_metadata_from_manifest(manifest)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    output_dir = infer_result_dir(manifest_path, audio_path, args.output_dir)
    ensure_dir(output_dir)

    video_id = manifest.get("id") or audio_path.stem
    output_stem = f"{video_id}_transcript"
    json_path = output_dir / f"{output_stem}.json"
    md_path = output_dir / f"{output_stem}.md"

    info_data, segments = transcribe_audio(audio_path, args)
    payload = {
        "bvid": video_id,
        "title": manifest.get("title"),
        "url": manifest.get("url"),
        "uploader": metadata.get("uploader"),
        "duration_string": metadata.get("duration_string"),
        "source": "faster-whisper",
        "audio_path": path_to_posix(audio_path),
        **info_data,
        "segments": segments,
    }

    write_json(json_path, payload)
    write_markdown(md_path, payload)

    print(f"Audio: {path_to_posix(audio_path)}")
    print(f"JSON: {path_to_posix(json_path)}")
    print(f"Markdown: {path_to_posix(md_path)}")
    print(f"Segments: {len(segments)}")
    return {
        "audio_path": audio_path,
        "json_path": json_path,
        "markdown_path": md_path,
        "segments": segments,
        "payload": payload,
    }


if __name__ == "__main__":
    raise SystemExit(main())
