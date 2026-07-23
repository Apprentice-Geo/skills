from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scripts.asr.chunking import (
    DEFAULT_VAD_PARAMETERS,
    SAMPLE_RATE,
    decode_normalized_audio,
    detect_speech_samples,
)
from scripts.config import LANGUAGE_ID_MODEL_DIR
from scripts.model_artifacts import (
    LANGUAGE_ID_REQUIRED_FILES,
    model_has_required_files,
)
from scripts.process_logging import get_logger
from scripts.utils import path_to_posix

logger = get_logger(__name__)

LANGUAGE_DETECTION_MAX_SPEECH_SECONDS = 30
LANGUAGE_DETECTION_WARNING_THRESHOLD = 0.8


@dataclass(frozen=True)
class LanguageDetection:
    language: str
    probability: float


def _speech_sample(audio_path: Path) -> np.ndarray:
    audio = decode_normalized_audio(audio_path)
    intervals = detect_speech_samples(audio, DEFAULT_VAD_PARAMETERS)
    maximum = LANGUAGE_DETECTION_MAX_SPEECH_SECONDS * SAMPLE_RATE
    parts: list[np.ndarray] = []
    remaining = maximum
    for start, end in intervals:
        if remaining <= 0:
            break
        part = audio.samples[start : min(end, start + remaining)]
        if part.size:
            parts.append(part)
            remaining -= int(part.size)
    if not parts:
        raise RuntimeError("Language detection found no usable speech in the audio.")
    return np.concatenate(parts)


def detect_language(audio_path: Path) -> LanguageDetection:
    if not model_has_required_files(LANGUAGE_ID_MODEL_DIR, LANGUAGE_ID_REQUIRED_FILES):
        raise RuntimeError(
            "Language identification model is missing. Run "
            r"uv run --no-sync python -m scripts.setup.install_model "
            "--model faster-whisper or --model qwen3."
        )
    try:
        import torch
        from speechbrain.inference.classifiers import EncoderClassifier
    except ImportError as exc:
        raise RuntimeError(
            "Language identification dependencies are not installed. "
            "Run the Windows setup script again."
        ) from exc

    classifier = EncoderClassifier.from_hparams(
        source=path_to_posix(LANGUAGE_ID_MODEL_DIR),
        run_opts={"device": "cpu"},
    )
    if classifier is None:
        raise RuntimeError("Language identification model could not be loaded.")
    signal = torch.from_numpy(_speech_sample(audio_path)).unsqueeze(0)
    _posterior, score, _index, labels = classifier.classify_batch(signal)
    if not labels:
        raise RuntimeError("Language identification returned no language label.")
    language = str(labels[0]).split(":", 1)[0].strip().lower()
    probability = float(score.reshape(-1)[0].exp().item())
    if not language or not math.isfinite(probability):
        raise RuntimeError("Language identification returned an invalid result.")
    if probability < LANGUAGE_DETECTION_WARNING_THRESHOLD:
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
    return LanguageDetection(language, probability)
