import argparse
from pathlib import Path
from typing import Any

from asr_qwen3 import has_model_weights, transcribe_with_qwen3
from manifest_io import (
    infer_result_dir,
    load_manifest,
    load_metadata_from_manifest,
    resolve_manifest_path,
    resolve_path,
)
from runtime_options import TranscribeOptions
from transcript_output import write_markdown
from process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    get_logger,
    terminal_info,
)

from config import (
    DEFAULT_ASR_PROVIDER,
    DEFAULT_TRANSCRIBE_BEAM_SIZE,
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_DEVICE,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    DEFAULT_WHISPER_MODEL_DIR,
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ASR_MODEL_DIR,
    SKILL_ROOT,
)
from utils import ensure_dir, path_to_posix, write_json


SIMPLIFIED_CHINESE_PROMPT = "以下是普通话内容，请使用简体中文转写。"
logger = get_logger(__name__)


def default_model_path() -> str:
    if (DEFAULT_WHISPER_MODEL_DIR / "model.bin").exists():
        return path_to_posix(DEFAULT_WHISPER_MODEL_DIR)
    return "small"


def first_audio_from_manifest(manifest: dict[str, Any]) -> Path:
    audio_files = manifest.get("audio_files") or []
    if not audio_files:
        raise ValueError("Manifest does not contain audio_files. Run fetch_audio.py first.")
    return resolve_path(str(audio_files[0]))


def make_segment(segment: Any) -> dict[str, Any]:
    return {
        "id": segment.id,
        "start": round(float(segment.start), 3),
        "end": round(float(segment.end), 3),
        "text": segment.text.strip(),
    }


def is_chinese_language(language: str) -> bool:
    return language.lower().startswith("zh")


def make_simplified_chinese_converter() -> Any:
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise RuntimeError(
            "Chinese transcription requires opencc-python-reimplemented to normalize output to Simplified Chinese. "
            r"Run .\scripts\setup\setup_windows.bat again to sync dependencies."
        ) from exc

    return OpenCC("t2s")


def normalize_segments_for_language(segments: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    if not is_chinese_language(language):
        return segments

    converter = make_simplified_chinese_converter()
    for segment in segments:
        segment["text"] = converter.convert(str(segment.get("text") or ""))
        for word in segment.get("words") or []:
            word["word"] = converter.convert(str(word.get("word") or ""))
    return segments


def metadata_duration(metadata: dict[str, Any]) -> float | None:
    duration = metadata.get("duration")
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


def transcribe_audio(
    audio_path: Path,
    options: TranscribeOptions,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if options.asr_provider == "qwen3":
        if has_model_weights(QWEN3_ASR_MODEL_DIR) and has_model_weights(QWEN3_ALIGNER_MODEL_DIR):
            try:
                info_data, segment_list = transcribe_with_qwen3(
                    audio_path,
                    options.language,
                    metadata_duration(metadata or {}),
                )
                return info_data, segment_list, "qwen3-asr"
            except Exception as exc:
                logger.warning(
                    "Qwen3 ASR failed; falling back to faster-whisper: %s",
                    exc,
                    exc_info=True,
                )
        else:
            logger.warning(
                "Qwen3 local models not found; falling back to faster-whisper."
            )
        logger.warning(
            "Warning: Qwen3 unavailable; falling back to faster-whisper.",
            extra={"terminal": True},
        )

    from faster_whisper import WhisperModel

    model_path = options.model or default_model_path()
    model = WhisperModel(
        model_path,
        device=options.device,
        compute_type=options.compute_type,
        cpu_threads=options.cpu_threads,
        num_workers=options.num_workers,
    )

    segments, info = model.transcribe(
        path_to_posix(audio_path),
        language=options.language,
        beam_size=options.beam_size,
        vad_filter=True,
        initial_prompt=SIMPLIFIED_CHINESE_PROMPT if is_chinese_language(options.language) else None,
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
        help="ASR provider. Use qwen3 only when CUDA is available and Qwen3 dependencies/models were installed explicitly.",
    )
    parser.add_argument("--model", help="Model name or local faster-whisper model directory.")
    parser.add_argument("--language", default=DEFAULT_TRANSCRIBE_LANGUAGE)
    parser.add_argument("--device", default=DEFAULT_TRANSCRIBE_DEVICE)
    parser.add_argument("--compute-type", default=DEFAULT_TRANSCRIBE_COMPUTE_TYPE)
    parser.add_argument("--beam-size", type=int, default=DEFAULT_TRANSCRIBE_BEAM_SIZE)
    parser.add_argument("--cpu-threads", type=int, default=0)
    parser.add_argument("--num-workers", type=int, default=1)
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

    info_data, segments, source = transcribe_audio(audio_path, options, metadata)
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
    write_markdown(md_path, payload)

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
