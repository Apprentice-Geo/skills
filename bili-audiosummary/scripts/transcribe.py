import argparse
from pathlib import Path
from typing import Any

from scripts.asr import parallel as parallel_asr
from scripts.asr.common import (
    is_chinese_language,
    make_segment,
    normalize_segments_for_language,
)
from scripts.asr.qwen3 import transcribe_with_qwen3
from scripts.manifest_io import (
    infer_result_dir,
    load_manifest,
    load_metadata_from_manifest,
    resolve_manifest_path,
    resolve_path,
)
from scripts.runtime_options import TranscribeOptions
from scripts.transcript_output import write_markdown_from_json
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    get_logger,
    terminal_info,
)

from scripts.config import (
    DEFAULT_ASR_PROVIDER,
    DEFAULT_TRANSCRIBE_BEAM_SIZE,
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_DEVICE,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    DEFAULT_WHISPER_MODEL_DIR,
    SKILL_ROOT,
)
from scripts.utils import ensure_dir, path_to_posix, write_json


logger = get_logger(__name__)


def default_model_path() -> str:
    if (DEFAULT_WHISPER_MODEL_DIR / "model.bin").exists():
        return path_to_posix(DEFAULT_WHISPER_MODEL_DIR)
    raise RuntimeError(
        "Local faster-whisper model is missing. Run "
        r"uv run --no-sync python -m scripts.setup.install_model --model faster-whisper "
        "before using faster-whisper ASR."
    )


def first_audio_from_manifest(manifest: dict[str, Any]) -> Path:
    audio_files = manifest.get("audio_files") or []
    if not audio_files:
        raise ValueError("Manifest does not contain audio_files. Run fetch_audio.py first.")
    return resolve_path(str(audio_files[0]))


def metadata_duration(metadata: dict[str, Any]) -> float | None:
    duration = metadata.get("duration")
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


def transcribe_whisper_audio(
    audio_path: Path,
    options: TranscribeOptions,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    from faster_whisper import WhisperModel

    model_path = options.model or default_model_path()
    model = WhisperModel(
        model_path,
        device=options.device,
        compute_type=options.compute_type,
        cpu_threads=options.cpu_threads if options.cpu_threads is not None else 0,
        num_workers=options.num_workers if options.num_workers is not None else 1,
    )

    segments, info = model.transcribe(
        path_to_posix(audio_path),
        language=options.language,
        beam_size=options.beam_size,
        vad_filter=True,
    )

    segment_list = normalize_segments_for_language(
        [make_segment(segment) for segment in segments],
        options.language,
    )
    info_data = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "duration_after_vad": getattr(info, "duration_after_vad", None),
        "model": model_path,
        "device": options.device,
        "compute_type": options.compute_type,
        "beam_size": options.beam_size,
        "text_normalization": "simplified-chinese" if is_chinese_language(options.language) else None,
    }
    return info_data, segment_list, "faster-whisper"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a fetched Bilibili audio file."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to fetch_manifest.json or an audio file. Defaults to --manifest/--audio if provided.",
    )
    parser.add_argument("--manifest", type=Path, help="Path to resource/fetch_manifest.json.")
    parser.add_argument("--audio", type=Path, help="Path to an audio file.")
    parser.add_argument("--output-dir", type=Path, help="Result directory for transcript outputs.")
    parser.add_argument(
        "--asr-provider",
        choices=("whisper", "qwen3"),
        default=DEFAULT_ASR_PROVIDER,
        help="Strict ASR provider. qwen3 requires CUDA plus explicitly installed dependencies and models; failures do not fall back to whisper.",
    )
    parser.add_argument("--model", help="Model name or local faster-whisper model directory.")
    parser.add_argument("--language", default=DEFAULT_TRANSCRIBE_LANGUAGE)
    parser.add_argument("--device", default=DEFAULT_TRANSCRIBE_DEVICE)
    parser.add_argument("--compute-type", default=DEFAULT_TRANSCRIBE_COMPUTE_TYPE)
    parser.add_argument("--beam-size", type=int, default=DEFAULT_TRANSCRIBE_BEAM_SIZE)
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=None,
        help="CPU threads per faster-whisper worker. Omit to plan automatically; explicit values are validated strictly.",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=None,
        help="Parallel faster-whisper workers. Omit to plan automatically; explicit values are validated strictly.",
    )
    return parser.parse_args()


def resolve_inputs(
    args: argparse.Namespace | TranscribeOptions,
) -> tuple[Path | None, Path, dict[str, Any]]:
    options = TranscribeOptions.from_args(args)
    manifest_path = options.manifest
    audio_path = options.audio

    if options.input:
        input_path = resolve_path(options.input)
        if input_path.suffix.lower() == ".json":
            manifest_path = input_path
        else:
            audio_path = input_path

    manifest: dict[str, Any] = {}
    if manifest_path:
        manifest_path = resolve_manifest_path(manifest_path)
        manifest = load_manifest(manifest_path)

    if audio_path:
        audio_path = resolve_path(path_to_posix(audio_path))
    elif manifest:
        audio_path = first_audio_from_manifest(manifest)
    else:
        raise ValueError("Provide a fetch manifest or an audio file.")

    return manifest_path, audio_path, manifest


def main() -> int:
    options = TranscribeOptions.from_args(parse_args())
    log_path = create_timestamped_log_path(
        SKILL_ROOT / ".cache" / "logs",
        "transcribe",
    )
    with LoggingSession(log_path) as session:
        try:
            run_transcribe(options)
        except Exception as exc:
            session.report_failure(exc)
            return 1
    return 0


def run_transcribe(args: argparse.Namespace | TranscribeOptions) -> dict[str, Any]:
    options = TranscribeOptions.from_args(args)
    terminal_info(
        logger,
        "[Stage] Transcribe audio with %s",
        options.asr_provider,
    )
    manifest_path, audio_path, manifest = resolve_inputs(options)
    metadata = load_metadata_from_manifest(manifest)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    output_dir = infer_result_dir(manifest_path, audio_path, options.output_dir)
    ensure_dir(output_dir)
    session = LoggingSession.current()
    if session is not None:
        session.move_to(output_dir)

    video_id = manifest.get("id") or audio_path.stem
    output_stem = f"{video_id}_transcript"
    json_path = output_dir / f"{output_stem}.json"
    md_path = output_dir / f"{output_stem}.md"

    if options.asr_provider == "whisper":
        duration = parallel_asr.probe_audio_duration(audio_path)
        info_data, segments, source = parallel_asr.run_parallel_whisper_transcribe(
            audio_path,
            options,
            output_dir,
            duration,
        )
    elif options.asr_provider == "qwen3":
        info_data, segments = transcribe_with_qwen3(
            audio_path,
            options.language,
            metadata_duration(metadata),
        )
        source = "qwen3-asr"
    else:
        raise ValueError(f"Unsupported ASR provider: {options.asr_provider}")
    payload = {
        "bvid": video_id,
        "title": manifest.get("title"),
        "url": manifest.get("url"),
        "uploader": metadata.get("uploader"),
        "duration_string": metadata.get("duration_string"),
        "source": source,
        "audio_path": path_to_posix(audio_path),
        **info_data,
        "segments": segments,
    }

    write_json(json_path, payload)
    write_markdown_from_json(json_path, md_path, normalize_segments=True)

    logger.info("Audio: %s", path_to_posix(audio_path))
    logger.info("JSON: %s", path_to_posix(json_path))
    logger.info("Markdown: %s", path_to_posix(md_path))
    logger.info("Segments: %d", len(segments))
    return {
        "audio_path": audio_path,
        "json_path": json_path,
        "markdown_path": md_path,
        "segments": segments,
        "payload": payload,
    }


if __name__ == "__main__":
    raise SystemExit(main())
