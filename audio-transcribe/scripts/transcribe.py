from __future__ import annotations

import argparse
import importlib.util
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
from audio_transcribe_contract import (
    PUBLIC_SCHEMA_VERSION,
    ResultValidationError,
    load_result,
)

from scripts.artifacts import (
    load_workspace_result,
    matching_manifest,
    publish_result,
    result_lock,
    write_workspace_result,
)
from scripts.asr.alignment import (
    ALIGNMENT_POLICY,
    AlignedTranscript,
    accept_provider_transcript,
)
from scripts.asr.pipeline_types import PipelineOutcome
from scripts.io_utils import canonical_sha256, sha256_file
from scripts.process_logging import LoggingSession, filtered_log_messages, get_logger
from scripts.text_normalization import TEXT_NORMALIZATION_POLICY

# 采样率 16kHz
SAMPLE_RATE = 16_000
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_DIR = SKILL_ROOT / "results"
MODELS_DIR = SKILL_ROOT / "models"
SUPPORTED_PROVIDERS = ("faster-whisper", "qwen3-asr")
QWEN3_ASR_LANGUAGES = frozenset(
    {"de", "en", "es", "fr", "it", "ja", "ko", "pt", "ru", "yue", "zh"}
)
logger = get_logger(__name__)


class Engine(Protocol):
    def __call__(
        self, samples: Any, request: dict[str, Any], execution: dict[str, Any]
    ) -> AlignedTranscript: ...


@dataclass(frozen=True)
class TranscribeOutcome:
    manifest_path: Path
    pipeline_outcome: PipelineOutcome | None


def _model_has_weights(directory: Path, pattern: str) -> bool:
    from scripts.model_artifacts import model_has_weights

    return model_has_weights(directory, (pattern,))


def _decode_audio(path: Path) -> Any:
    try:
        from faster_whisper.audio import decode_audio
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is required to decode local audio. Run setup first."
        ) from exc
    return decode_audio(str(path), sampling_rate=SAMPLE_RATE)


def _detect_language(samples: Any) -> str:
    model_dir = MODELS_DIR / "lang-id-voxlingua107-ecapa"
    required = ("embedding_model.ckpt", "classifier.ckpt", "hyperparams.yaml")
    if not all((model_dir / name).is_file() for name in required):
        raise RuntimeError(
            "Language identification model is missing. Pass --language or run setup."
        )
    try:
        import torch
        from speechbrain.inference.classifiers import EncoderClassifier
    except ImportError as exc:
        raise RuntimeError(
            "SpeechBrain language identification dependencies are missing."
        ) from exc
    # SpeechBrain 1.1 registers deprecated optional aliases as lazy modules.
    # Removing those unused aliases prevents Python inspection from importing
    # optional k2 while HyperPyYAML resolves this local ECAPA model.
    for module_name in (
        "speechbrain.pretrained",
        "speechbrain.k2_integration",
        "speechbrain.wordemb",
        "speechbrain.lobes.models.huggingface_transformers",
        "speechbrain.lobes.models.spacy",
        "speechbrain.lobes.models.flair",
        "speechbrain.nnet.loss.transducer_loss",
    ):
        sys.modules.pop(module_name, None)
    classifier = EncoderClassifier.from_hparams(
        source=str(model_dir),
        overrides={"pretrained_path": str(model_dir)},
        run_opts={"device": "cpu"},
    )
    if classifier is None:
        raise RuntimeError("Language identification model could not be loaded.")
    signal = torch.from_numpy(samples[: 60 * SAMPLE_RATE]).unsqueeze(0)
    _posterior, score, _index, labels = classifier.classify_batch(signal)
    probability = float(score.reshape(-1)[0].exp().item())
    if not labels or not math.isfinite(probability):
        raise RuntimeError("Language identification returned an invalid result.")
    language = str(labels[0]).split(":", 1)[0].strip().lower()
    if not language:
        raise RuntimeError("Language identification returned an empty language.")
    if probability < 0.8:
        logger.warning(
            "Language detection confidence is low: language=%s probability=%.3f; "
            "continuing with the highest-scoring language.",
            language,
            probability,
        )
    else:
        logger.info(
            "Language detected: language=%s probability=%.3f",
            language,
            probability,
        )
    return language


def _language_detection_samples(
    samples: Any,
    speech_intervals: list[tuple[int, int]],
) -> np.ndarray:
    remaining = 30 * SAMPLE_RATE
    selected: list[np.ndarray] = []
    source = np.asarray(samples, dtype=np.float32)
    for start, end in speech_intervals:
        if remaining <= 0:
            break
        take = min(max(0, end - start), remaining)
        if take:
            selected.append(source[start : start + take])
            remaining -= take
    if not selected:
        raise RuntimeError("Language detection requires usable speech.")
    return np.concatenate(selected)


def _qwen3_asr_ready() -> bool:
    if (
        importlib.util.find_spec("qwen_asr") is None
        or importlib.util.find_spec("torch") is None
    ):
        return False
    if not _model_has_weights(MODELS_DIR / "qwen3-asr-0.6b", "model*.safetensors"):
        return False
    if not _model_has_weights(
        MODELS_DIR / "qwen3-forcedaligner-0.6b", "model*.safetensors"
    ):
        return False
    import torch

    return bool(torch.cuda.is_available())


def _whisper_ready() -> bool:
    return importlib.util.find_spec(
        "faster_whisper"
    ) is not None and _model_has_weights(
        MODELS_DIR / "faster-whisper-small", "model.bin"
    )


def _select_provider(requested: str | None, language: str) -> str:
    if requested is not None:
        if requested not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported transcription provider: {requested}")
        if requested == "qwen3-asr" and language not in QWEN3_ASR_LANGUAGES:
            supported = ", ".join(sorted(QWEN3_ASR_LANGUAGES))
            raise ValueError(
                f"Qwen3-ASR does not support language '{language}'. Supported: {supported}."
            )
        return requested
    if language in QWEN3_ASR_LANGUAGES and _qwen3_asr_ready():
        return "qwen3-asr"
    if _whisper_ready():
        return "faster-whisper"
    raise RuntimeError(
        "No transcription Provider is ready. Install Qwen3-ASR or faster-whisper first."
    )


def run_transcribe(
    audio_path: Path,
    *,
    language: str | None = None,
    provider: str | None = None,
    beam_size: int = 5,
    compute_type: str = "float32",
    cpu_threads: int | None = None,
    num_workers: int | None = None,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    decoder: Callable[[Path], Any] = _decode_audio,
    vad_detector: Callable[[Any], list[tuple[int, int]]] | None = None,
    language_detector: Callable[[Any], str] = _detect_language,
    engine: Engine | None = None,
    prepared_model: Any | None = None,
) -> TranscribeOutcome:
    audio_path = audio_path.resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Hashing is streamed, so audio identity does not depend on the input path or
    # require loading the whole source file into a second memory buffer.
    audio_id = sha256_file(audio_path)
    size = audio_path.stat().st_size
    samples = decoder(audio_path)
    sample_count = int(len(samples))
    if sample_count <= 0:
        raise ValueError("Decoded audio is empty.")
    from scripts.asr.chunking import (
        DEFAULT_VAD_PARAMETERS,
        NormalizedAudio,
        detect_speech_samples,
    )

    normalized_audio = NormalizedAudio(
        np.asarray(samples, dtype=np.float32), SAMPLE_RATE
    )
    speech_intervals: list[tuple[int, int]] | None = None
    if language is None:
        speech_intervals = (
            vad_detector(normalized_audio)
            if vad_detector is not None
            else detect_speech_samples(normalized_audio, DEFAULT_VAD_PARAMETERS)
        )
    detection_samples = (
        _language_detection_samples(samples, speech_intervals or [])
        if language is None
        else samples
    )
    resolved_language = (
        (language or language_detector(detection_samples)).strip().lower()
    )
    if not resolved_language:
        raise ValueError("Resolved language must not be empty.")
    provider = _select_provider(provider, resolved_language)
    from scripts.asr.execution import Qwen3AsrCudaPolicy, WhisperCpuPolicy
    from scripts.asr.providers import Qwen3AsrProvider, WhisperProvider
    from scripts.runtime_options import TranscribeOptions

    if provider == "faster-whisper":
        options = TranscribeOptions(
            language=resolved_language,
            compute_type=compute_type,
            beam_size=beam_size,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
        )
        provider_strategy = WhisperProvider(options)
        policy = WhisperCpuPolicy(options)
    else:
        provider_strategy = Qwen3AsrProvider(resolved_language)
        policy = Qwen3AsrCudaPolicy()
    execution = policy.execution_identity(sample_count)
    canonical_request = {
        "provider": provider,
        "language": resolved_language,
        "provider_identity": provider_strategy.request_identity(),
        "execution_policy": execution,
        "vad_parameters": asdict(DEFAULT_VAD_PARAMETERS),
        "planning_parameters": asdict(policy.planning_parameters),
        "segmentation_schema_version": 1,
        "public_schema_version": PUBLIC_SCHEMA_VERSION,
        "text_normalization": TEXT_NORMALIZATION_POLICY,
        "alignment_policy": dict(ALIGNMENT_POLICY),
    }
    config_digest = canonical_sha256(canonical_request)
    request = {"config_digest": config_digest, **canonical_request}
    result_dir = (
        results_dir / audio_id / (f"{provider}-{resolved_language}-{config_digest}")
    )
    manifest_path = result_dir / "manifest.json"
    audio = {
        "id": audio_id,
        "size": size,
        "sample_count": sample_count,
        "sample_rate": SAMPLE_RATE,
        "duration": sample_count / SAMPLE_RATE,
    }

    with result_lock(result_dir):
        if matching_manifest(manifest_path, audio=audio, request=request) is not None:
            try:
                load_result(manifest_path)
            except ResultValidationError:
                pass
            else:
                return TranscribeOutcome(manifest_path.resolve(), None)

        log_path = result_dir / "transcribe.log"
        with LoggingSession(log_path, mode="a" if manifest_path.exists() else "w"):
            logger.info(
                "Transcription invocation: input=%s audio_id=%s config_digest=%s",
                audio_path,
                audio_id,
                config_digest,
            )
            workspace_path = result_dir / "workspace" / "result.json"
            workspace_valid = False
            if workspace_path.exists():
                try:
                    load_workspace_result(
                        workspace_path,
                        expected_audio_id=audio_id,
                        expected_config_digest=config_digest,
                        expected_provider=provider,
                        expected_language=resolved_language,
                        expected_duration=audio["duration"],
                    )
                except ResultValidationError:
                    logger.warning(
                        "Workspace result invalid: rebuilding from pipeline state."
                    )
                else:
                    workspace_valid = True
            pipeline_outcome: PipelineOutcome | None = None
            if not workspace_valid:
                if engine is None:
                    from scripts.asr.pipeline import run_asr_pipeline

                    if provider == "faster-whisper":
                        assert isinstance(provider_strategy, WhisperProvider)
                        assert isinstance(policy, WhisperCpuPolicy)
                        pipeline_outcome = run_asr_pipeline(
                            audio_path,
                            result_dir / "workspace",
                            provider_strategy,
                            policy,
                            audio_id=audio_id,
                            config_digest=config_digest,
                            prepared_audio=normalized_audio,
                            prepared_vad=speech_intervals,
                            prepared_model=prepared_model,
                            vad_detector=vad_detector,
                        )
                    else:
                        assert isinstance(provider_strategy, Qwen3AsrProvider)
                        assert isinstance(policy, Qwen3AsrCudaPolicy)
                        pipeline_outcome = run_asr_pipeline(
                            audio_path,
                            result_dir / "workspace",
                            provider_strategy,
                            policy,
                            audio_id=audio_id,
                            config_digest=config_digest,
                            prepared_audio=normalized_audio,
                            prepared_vad=speech_intervals,
                            prepared_model=prepared_model,
                            vad_detector=vad_detector,
                        )
                else:
                    candidate = engine(samples, canonical_request, execution)
                    result, report = accept_provider_transcript(
                        candidate,
                        duration=audio["duration"],
                        chunk_index="whole_audio",
                        language=resolved_language,
                    )
                    if report.dropped_zero_duration_items:
                        logger.warning(
                            "ASR timestamp cleanup: provider=%s chunk=whole_audio "
                            "action=drop_zero_duration_items dropped=%d "
                            "first_start=%.3f last_end=%.3f",
                            provider,
                            report.dropped_zero_duration_items,
                            report.first_start,
                            report.last_end,
                        )
                    write_workspace_result(
                        workspace_path,
                        audio_id=audio_id,
                        config_digest=config_digest,
                        text=result.text,
                        items=list(result.items),
                        duration=audio["duration"],
                        provider=provider,
                        language=resolved_language,
                    )
            manifest = publish_result(
                result_dir,
                audio=audio,
                request=request,
                replace_existing=True,
            ).resolve()
            return TranscribeOutcome(manifest, pipeline_outcome)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe a local audio file.")
    parser.add_argument("audio_path", type=Path)
    parser.add_argument("--language")
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--compute-type", default="float32")
    parser.add_argument("--cpu-threads", type=int)
    parser.add_argument("--num-workers", type=int)
    return parser.parse_args(argv)


def _format_elapsed(seconds: float) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours >= 1:
        return f"{int(hours)}h {int(minutes)}m {seconds:05.2f}s"
    if minutes >= 1:
        return f"{int(minutes)}m {seconds:05.2f}s"
    return f"{seconds:.2f}s"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    try:
        with filtered_log_messages():
            outcome = run_transcribe(
                args.audio_path,
                language=args.language,
                provider=args.provider,
                beam_size=args.beam_size,
                compute_type=args.compute_type,
                cpu_threads=args.cpu_threads,
                num_workers=args.num_workers,
            )
    except Exception as exc:
        logger.exception("Transcription failed.")
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started
    print(f"[Stage] Transcribe completed in {_format_elapsed(elapsed)}")
    print(f"manifest: {outcome.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
