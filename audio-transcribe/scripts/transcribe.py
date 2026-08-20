from __future__ import annotations

import argparse
import importlib.util
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
from audio_transcribe_contract import load_result

from scripts.alignment import AlignmentItem
from scripts.artifacts import (
    publish_result,
    recover_public_artifacts,
    variant_lock,
    write_workspace_result,
)
from scripts.io_utils import canonical_sha256, sha256_file
from scripts.model_identity import provider_model_identity
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
QWEN3_ASR_LANGUAGE_NAMES = {
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pt": "Portuguese",
    "ru": "Russian",
    "yue": "Cantonese",
    "zh": "Chinese",
}

logger = get_logger(__name__)


@dataclass(frozen=True)
class EngineResult:
    text: str
    items: list[AlignmentItem]


class Engine(Protocol):
    def __call__(
        self, samples: Any, request: dict[str, Any], execution: dict[str, Any]
    ) -> EngineResult: ...


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


def _execution_identity(
    provider: str,
    *,
    cpu_threads: int | None,
    num_workers: int | None,
) -> dict[str, Any]:
    if provider == "qwen3-asr":
        return {
            "policy": "qwen3-asr-cuda",
            "batch_size": 4,
            "batch_isolation": True,
        }
    budget = max(1, math.floor((os.cpu_count() or 1) * 0.75))
    for value, name in ((cpu_threads, "cpu_threads"), (num_workers, "num_workers")):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise ValueError(f"{name} must be a positive integer.")
    workers = num_workers or 1
    threads = cpu_threads or max(1, budget // workers)
    if workers * threads > budget:
        raise ValueError("Whisper worker configuration exceeds the CPU budget.")
    return {
        "policy": "whisper-cpu",
        "cpu_budget": budget,
        "num_workers": workers,
        "cpu_threads": threads,
        "count_strategy": "divisible",
    }


def resolve_request(
    *,
    provider: str,
    language: str,
    beam_size: int,
    compute_type: str,
    execution: dict[str, Any],
) -> dict[str, Any]:
    if beam_size < 1:
        raise ValueError("beam_size must be positive.")
    model = provider_model_identity(provider)
    request: dict[str, Any] = {
        "provider": provider,
        "language": language,
        "model": model,
        "execution_policy": execution,
        "vad_parameters": {"schema_version": 1, "method": "silero"},
        "planning_parameters": {
            "schema_version": 1,
            "sample_rate": SAMPLE_RATE,
            "min_chunk_seconds": 30,
            "max_chunk_seconds": 300,
        },
        "segmentation_schema_version": 1,
        "text_normalization": TEXT_NORMALIZATION_POLICY,
    }
    if provider == "faster-whisper":
        request.update(
            {
                "beam_size": beam_size,
                "compute_type": compute_type,
                "device": "cpu",
                "word_timestamps": True,
            }
        )
    else:
        request.update(
            {
                "device": "cuda:0",
                "compute_type": "bfloat16",
                "max_new_tokens": 1024,
                "return_time_stamps": True,
            }
        )
    return request


def _run_faster_whisper(
    samples: Any, request: dict[str, Any], execution: dict[str, Any]
) -> EngineResult:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed.") from exc
    model_path = MODELS_DIR / "faster-whisper-small"
    if not _model_has_weights(model_path, "model.bin"):
        raise RuntimeError("The local faster-whisper model is missing.")
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type=request["compute_type"],
        cpu_threads=execution["cpu_threads"],
        num_workers=execution["num_workers"],
    )
    raw_segments, _info = model.transcribe(
        samples,
        language=request["language"],
        beam_size=request["beam_size"],
        vad_filter=True,
        word_timestamps=True,
    )
    segments = list(raw_segments)
    # Keep Provider text unchanged; public artifacts must not run OpenCC or any
    # other text normalization.
    text = "".join(str(getattr(segment, "text", "") or "") for segment in segments)
    items = [
        AlignmentItem(
            text=str(getattr(word, "word", "") or ""),
            start=round(float(word.start), 3),
            end=round(float(word.end), 3),
            probability=(
                float(probability)
                if (probability := getattr(word, "probability", None)) is not None
                else None
            ),
        )
        for segment in segments
        for word in (getattr(segment, "words", None) or [])
    ]
    return EngineResult(text, items)


def _run_qwen3_asr(
    samples: Any, request: dict[str, Any], execution: dict[str, Any]
) -> EngineResult:
    try:
        import torch
        from qwen_asr import Qwen3ASRModel
        from transformers import GenerationConfig
    except ImportError as exc:
        raise RuntimeError("Qwen3-ASR dependencies are not installed.") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("Qwen3-ASR requires an available CUDA GPU.")
    model_path = MODELS_DIR / "qwen3-asr-0.6b"
    aligner_path = MODELS_DIR / "qwen3-forcedaligner-0.6b"
    if not _model_has_weights(
        model_path, "model*.safetensors"
    ) or not _model_has_weights(aligner_path, "model*.safetensors"):
        raise RuntimeError("The local Qwen3-ASR models are missing.")
    dtype = torch.bfloat16
    generation_config = GenerationConfig.from_pretrained(
        str(model_path), temperature=None
    )
    model = Qwen3ASRModel.from_pretrained(
        str(model_path),
        forced_aligner=str(aligner_path),
        forced_aligner_kwargs={"dtype": dtype, "device_map": "cuda:0"},
        dtype=dtype,
        device_map="cuda:0",
        max_inference_batch_size=execution["batch_size"],
        max_new_tokens=request["max_new_tokens"],
        generation_config=generation_config,
    )
    results = model.transcribe(
        [(samples, SAMPLE_RATE)],
        language=QWEN3_ASR_LANGUAGE_NAMES[request["language"]],
        return_time_stamps=True,
    )
    if len(results) != 1:
        raise RuntimeError("Qwen3-ASR returned an unexpected result count.")
    result = results[0]
    timestamp_data = getattr(result, "time_stamps", None)
    items = [
        AlignmentItem(
            text=str(getattr(item, "text", "") or ""),
            start=round(float(item.start_time), 3),
            end=round(float(item.end_time), 3),
            probability=None,
        )
        for item in list(getattr(timestamp_data, "items", []) or [])
        if getattr(item, "start_time", None) is not None
        and getattr(item, "end_time", None) is not None
    ]
    return EngineResult(str(getattr(result, "text", "") or ""), items)


def _default_engine(
    samples: Any, request: dict[str, Any], execution: dict[str, Any]
) -> EngineResult:
    if request["provider"] == "faster-whisper":
        return _run_faster_whisper(samples, request, execution)
    return _run_qwen3_asr(samples, request, execution)


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
    engine: Engine = _default_engine,
) -> Path:
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
    speech_intervals = (
        vad_detector(normalized_audio)
        if vad_detector is not None
        else detect_speech_samples(normalized_audio, DEFAULT_VAD_PARAMETERS)
    )
    detection_samples = (
        _language_detection_samples(samples, speech_intervals)
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
        "text_normalization": TEXT_NORMALIZATION_POLICY,
    }
    variant_id = canonical_sha256(canonical_request)
    request = {"variant_id": variant_id, **canonical_request}
    variant_dir = (
        results_dir / audio_id / (f"{provider}-{resolved_language}-{variant_id}")
    )
    manifest_path = variant_dir / "result_manifest.json"
    audio = {
        "id": audio_id,
        "size": size,
        "sample_count": sample_count,
        "sample_rate": SAMPLE_RATE,
        "duration": sample_count / SAMPLE_RATE,
    }

    with variant_lock(variant_dir):
        if manifest_path.exists():
            try:
                load_result(manifest_path)
            except ValueError:
                recover_public_artifacts(manifest_path)
            return manifest_path.resolve()

        log_path = variant_dir / "transcribe.log"
        with LoggingSession(log_path, mode="w"):
            logger.info(
                "Transcription invocation: input=%s audio_id=%s variant_id=%s",
                audio_path,
                audio_id,
                variant_id,
            )
            workspace_path = variant_dir / "workspace" / "result.json"
            if not workspace_path.exists():
                if engine is _default_engine:
                    from scripts.asr.pipeline import run_asr_pipeline

                    if provider == "faster-whisper":
                        assert isinstance(provider_strategy, WhisperProvider)
                        assert isinstance(policy, WhisperCpuPolicy)
                        run_asr_pipeline(
                            audio_path,
                            variant_dir / "workspace",
                            provider_strategy,
                            policy,
                            prepared_audio=normalized_audio,
                            prepared_vad=speech_intervals,
                        )
                    else:
                        assert isinstance(provider_strategy, Qwen3AsrProvider)
                        assert isinstance(policy, Qwen3AsrCudaPolicy)
                        run_asr_pipeline(
                            audio_path,
                            variant_dir / "workspace",
                            provider_strategy,
                            policy,
                            prepared_audio=normalized_audio,
                            prepared_vad=speech_intervals,
                        )
                else:
                    result = engine(samples, canonical_request, execution)
                    write_workspace_result(
                        workspace_path,
                        text=result.text,
                        items=result.items,
                        duration=audio["duration"],
                        provider=provider,
                        language=resolved_language,
                    )
            manifest = publish_result(
                variant_dir,
                audio=audio,
                request=request,
            ).resolve()
            return manifest


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
            manifest_path = run_transcribe(
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
    print(f"result_manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
