from __future__ import annotations

import time
from typing import Any

import numpy as np

from scripts.asr.alignment import TranscriptWord
from scripts.asr.chunking import ChunkLayout
from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript
from scripts.config import DEFAULT_WHISPER_MODEL_DIR
from scripts.model_artifacts import WHISPER_WEIGHT_PATTERNS, model_has_weights
from scripts.model_identity import provider_model_identity
from scripts.process_logging import get_logger
from scripts.runtime_options import TranscribeOptions
from scripts.utils import path_to_posix

logger = get_logger(__name__)


class WhisperProvider:
    name = "faster-whisper"
    source = "faster-whisper"

    def __init__(self, options: TranscribeOptions) -> None:
        if options.language is None:
            raise ValueError("WhisperProvider requires a resolved language.")
        self.options = options
        self.language = options.language
        self.model_path = options.model_path or path_to_posix(DEFAULT_WHISPER_MODEL_DIR)

    def request_identity(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "language": self.language,
            "model": provider_model_identity(self.name),
            "beam_size": self.options.beam_size,
            "device": self.options.device,
            "compute_type": self.options.compute_type,
            "word_timestamps": True,
        }

    def prepare(self, execution_identity: dict[str, Any]) -> Any:
        if not self.options.model_path and not model_has_weights(
            DEFAULT_WHISPER_MODEL_DIR, WHISPER_WEIGHT_PATTERNS
        ):
            raise RuntimeError(
                "Local faster-whisper model is missing. Run "
                r"uv run --no-sync python -m scripts.setup.install_model --model faster-whisper "
                "before using faster-whisper ASR."
            )
        from faster_whisper import WhisperModel

        logger.info(
            "ASR model prepare: provider=%s model=%s device=%s compute_type=%s "
            "policy=%s num_workers=%d cpu_threads=%d",
            self.name,
            self.model_path,
            self.options.device,
            self.options.compute_type,
            execution_identity["policy"],
            int(execution_identity["num_workers"]),
            int(execution_identity["cpu_threads"]),
        )
        return WhisperModel(
            self.model_path,
            device=self.options.device,
            compute_type=self.options.compute_type,
            cpu_threads=int(execution_identity["cpu_threads"]),
            num_workers=int(execution_identity["num_workers"]),
        )

    def transcribe_one(
        self, prepared: Any, samples: np.ndarray, layout: ChunkLayout
    ) -> ChunkTranscript:
        started = time.perf_counter()
        raw_segments, info = prepared.transcribe(
            samples,
            language=self.language,
            beam_size=self.options.beam_size,
            vad_filter=True,
            word_timestamps=True,
        )
        segments = list(raw_segments)
        text = "".join(str(getattr(segment, "text", "") or "") for segment in segments)
        words = tuple(
            TranscriptWord(
                text=str(getattr(word, "word", "") or ""),
                start=float(word.start),
                end=float(word.end),
                probability=(
                    float(probability)
                    if (probability := getattr(word, "probability", None)) is not None
                    else None
                ),
            )
            for segment in segments
            for word in (getattr(segment, "words", None) or [])
        )
        transcript = ChunkTranscript(
            layout.index,
            layout.start_sample,
            layout.end_sample,
            text,
            words,
            {
                "language": getattr(info, "language", None),
                "language_probability": getattr(info, "language_probability", None),
                "duration": getattr(info, "duration", None),
                "duration_after_vad": getattr(info, "duration_after_vad", None),
            },
            round(time.perf_counter() - started, 3),
        )
        return transcript

    def final_info(self, plan: AsrPipelinePlan, words_present: bool) -> dict[str, Any]:
        request = plan.provider_request
        return {
            "language": self.language,
            "language_probability": None,
            "duration": plan.source.duration,
            "duration_after_vad": None,
            "model": request["model"],
            "device": request["device"],
            "compute_type": request["compute_type"],
            "beam_size": request["beam_size"],
            "word_timestamps": words_present,
            "text_normalization": None,
        }

    def postprocess_segments(
        self, segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        # Preserve Provider text exactly; public consumers must not receive an
        # OpenCC rewrite or any other transcript normalization.
        return [{**segment} for segment in segments]
