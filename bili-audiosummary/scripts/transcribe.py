import argparse
import importlib.util
from dataclasses import replace
from pathlib import Path
from typing import Any

from scripts.asr.execution import Qwen3CudaPolicy, WhisperCpuPolicy
from scripts.asr.pipeline import run_asr_pipeline
from scripts.asr.providers import Qwen3Provider, WhisperProvider
from scripts.config import (
    DEFAULT_TRANSCRIBE_BEAM_SIZE,
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_DEVICE,
    DEFAULT_WHISPER_MODEL_DIR,
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ASR_MODEL_DIR,
    SKILL_ROOT,
)
from scripts.language_detection import LanguageDetection, detect_language
from scripts.manifest_io import (
    infer_result_dir,
    load_manifest,
    load_metadata_from_manifest,
    resolve_manifest_path,
    resolve_path,
)
from scripts.model_artifacts import (
    QWEN3_WEIGHT_PATTERNS,
    WHISPER_WEIGHT_PATTERNS,
    model_has_weights,
)
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    get_logger,
    terminal_info,
)
from scripts.runtime_options import TranscribeOptions
from scripts.transcript_output import write_markdown_from_json
from scripts.utils import ensure_dir, path_to_posix, write_json

logger = get_logger(__name__)

TRANSCRIBE_MODELS = ("qwen3", "faster-whisper")


def default_model_path() -> str:
    if model_has_weights(DEFAULT_WHISPER_MODEL_DIR, WHISPER_WEIGHT_PATTERNS):
        return path_to_posix(DEFAULT_WHISPER_MODEL_DIR)
    raise RuntimeError(
        "Local faster-whisper model is missing. Run "
        r"uv run --no-sync python -m scripts.setup.install_model --model faster-whisper "
        "before using faster-whisper ASR."
    )


def first_audio_from_manifest(manifest: dict[str, Any]) -> Path:
    audio_files = manifest.get("audio_files") or []
    if not audio_files:
        raise ValueError(
            "Manifest does not contain audio_files. Run fetch_audio.py first."
        )
    return resolve_path(str(audio_files[0]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a fetched Bilibili audio file."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to fetch_manifest.json or an audio file. Defaults to --manifest/--audio if provided.",
    )
    parser.add_argument(
        "--manifest", type=Path, help="Path to resource/fetch_manifest.json."
    )
    parser.add_argument("--audio", type=Path, help="Path to an audio file.")
    parser.add_argument(
        "--output-dir", type=Path, help="Result directory for transcript outputs."
    )
    parser.add_argument(
        "--model",
        choices=TRANSCRIBE_MODELS,
        help="Transcription model. Omit to prefer Qwen3 and otherwise use faster-whisper.",
    )
    parser.add_argument(
        "--language",
        help="Spoken language code. Omit to detect one language for the full audio.",
    )
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


def _qwen3_ready() -> bool:
    if (
        importlib.util.find_spec("qwen_asr") is None
        or importlib.util.find_spec("torch") is None
        or importlib.util.find_spec("torchaudio") is None
    ):
        return False
    if not model_has_weights(
        QWEN3_ASR_MODEL_DIR, QWEN3_WEIGHT_PATTERNS
    ) or not model_has_weights(QWEN3_ALIGNER_MODEL_DIR, QWEN3_WEIGHT_PATTERNS):
        return False
    import torch

    return bool(torch.cuda.is_available())


def _whisper_ready() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None and model_has_weights(
        DEFAULT_WHISPER_MODEL_DIR, WHISPER_WEIGHT_PATTERNS
    )


def resolve_transcribe_options(
    options: TranscribeOptions, audio_path: Path
) -> tuple[TranscribeOptions, LanguageDetection | None]:
    detection = None
    language = options.language
    if not language:
        detection = detect_language(audio_path)
        language = detection.language
    language = language.lower()

    requested_model = options.model
    if requested_model is None and options.asr_provider is not None:
        requested_model = (
            "faster-whisper"
            if options.asr_provider == "whisper"
            else options.asr_provider
        )
    if requested_model not in (None, *TRANSCRIBE_MODELS):
        raise ValueError(f"Unsupported transcription model: {requested_model}")

    if requested_model == "qwen3":
        if language not in Qwen3Provider.supported_languages:
            supported = ", ".join(sorted(Qwen3Provider.supported_languages))
            raise ValueError(
                f"Qwen3 does not support language '{language}' in the current "
                f"timestamped transcription pipeline. Supported: {supported}."
            )
        selected_model = "qwen3"
    elif requested_model == "faster-whisper":
        selected_model = "faster-whisper"
    elif language in Qwen3Provider.supported_languages and _qwen3_ready():
        selected_model = "qwen3"
    elif _whisper_ready():
        selected_model = "faster-whisper"
    else:
        raise RuntimeError(
            "No transcription model is ready. Install Qwen3 or faster-whisper "
            "dependencies and model files before retrying."
        )

    provider = "qwen3" if selected_model == "qwen3" else "whisper"
    return (
        replace(
            options,
            language=language,
            asr_provider=provider,
            model=None,
        ),
        detection,
    )


def run_transcribe(args: argparse.Namespace | TranscribeOptions) -> dict[str, Any]:
    options = TranscribeOptions.from_args(args)
    manifest_path, audio_path, manifest = resolve_inputs(options)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    options, detection = resolve_transcribe_options(options, audio_path)
    terminal_info(logger, "[Stage] Transcribe audio with %s", options.asr_provider)
    metadata = load_metadata_from_manifest(manifest)

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
        info_data, segments, source = run_asr_pipeline(
            audio_path,
            output_dir / "asr_parallel",
            WhisperProvider(options),
            WhisperCpuPolicy(options),
        )
    elif options.asr_provider == "qwen3":
        if options.language is None:
            raise RuntimeError("Resolved transcription language is missing.")
        info_data, segments, source = run_asr_pipeline(
            audio_path,
            output_dir / "asr_qwen3",
            Qwen3Provider(options.language),
            Qwen3CudaPolicy(),
        )
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
    if detection is not None:
        payload["language_detection"] = {
            "model": "speechbrain/lang-id-voxlingua107-ecapa",
            "probability": detection.probability,
        }

    write_json(json_path, payload)
    write_markdown_from_json(json_path, md_path)

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
