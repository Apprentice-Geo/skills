from __future__ import annotations

import os
import time
from typing import Any

import numpy as np

from scripts.asr.alignment import TranscriptWord
from scripts.asr.chunking import SAMPLE_RATE, ChunkLayout
from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript
from scripts.asr.prepared_model import PreparedModel, unwrap_model
from scripts.config import (
    DEFAULT_HF_ENDPOINT,
    QWEN3_ASR_ALIGNER_MODEL_DIR,
    QWEN3_ASR_DEVICE_MAP,
    QWEN3_ASR_DTYPE,
    QWEN3_ASR_MAX_NEW_TOKENS,
    QWEN3_ASR_MODEL_DIR,
)
from scripts.model_identity import validate_model
from scripts.process_logging import get_logger
from scripts.utils import path_to_posix

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
QWEN3_ASR_END_TIME_TOLERANCE_SECONDS = 0.1

logger = get_logger(__name__)


def adapt_qwen_timestamp_items(items: list[Any]) -> list[TranscriptWord]:
    normalized: list[TranscriptWord] = []
    for item in items:
        text = str(getattr(item, "text", "") or "")
        start = getattr(item, "start_time", None)
        end = getattr(item, "end_time", None)
        if start is None or end is None:
            continue
        normalized.append(TranscriptWord(text=text, start=float(start), end=float(end)))
    return normalized


class Qwen3AsrProvider:
    name = "qwen3-asr"
    source = "qwen3-asr"
    supported_languages = frozenset(QWEN3_ASR_LANGUAGE_NAMES)

    def __init__(self, language: str) -> None:
        self.language = language
        try:
            self.model_language = QWEN3_ASR_LANGUAGE_NAMES[language.lower()]
        except KeyError as exc:
            supported = ", ".join(sorted(QWEN3_ASR_LANGUAGE_NAMES))
            raise ValueError(
                f"Unsupported Qwen3-ASR language: {language}. Supported: {supported}"
            ) from exc

    def request_identity(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "language": self.language,
            "model_language": self.model_language,
            "model": validate_model(
                self.name, QWEN3_ASR_MODEL_DIR, QWEN3_ASR_ALIGNER_MODEL_DIR
            ),
            "device": QWEN3_ASR_DEVICE_MAP,
            "compute_type": QWEN3_ASR_DTYPE,
            "max_new_tokens": QWEN3_ASR_MAX_NEW_TOKENS,
            "return_time_stamps": True,
        }

    def prepare(self, execution_identity: dict[str, Any]) -> Any:
        verified_request = self.request_identity()
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
            from transformers import GenerationConfig
        except ImportError as exc:
            raise RuntimeError(
                "Qwen3-ASR dependencies are not installed. Run "
                r"uv sync --python 3.12 --no-dev --extra qwen3-asr, then "
                r"uv run --no-sync python -m scripts.setup.install_model --model qwen3-asr."
            ) from exc
        if not torch.cuda.is_available():
            raise RuntimeError(
                "Qwen3-ASR requires an available CUDA GPU. Use the default whisper provider on CPU."
            )
        os.environ.setdefault("HF_ENDPOINT", DEFAULT_HF_ENDPOINT)
        model_path = path_to_posix(QWEN3_ASR_MODEL_DIR)
        dtype = getattr(torch, QWEN3_ASR_DTYPE)
        logger.info(
            "ASR model prepare: provider=%s model=%s forced_aligner=%s "
            "device=%s compute_type=%s policy=%s batch_size=%d",
            self.name,
            model_path,
            path_to_posix(QWEN3_ASR_ALIGNER_MODEL_DIR),
            QWEN3_ASR_DEVICE_MAP,
            QWEN3_ASR_DTYPE,
            execution_identity["policy"],
            int(execution_identity["batch_size"]),
        )
        generation_config = GenerationConfig.from_pretrained(
            model_path, temperature=None
        )
        model = Qwen3ASRModel.from_pretrained(
            model_path,
            forced_aligner=path_to_posix(QWEN3_ASR_ALIGNER_MODEL_DIR),
            forced_aligner_kwargs={"dtype": dtype, "device_map": QWEN3_ASR_DEVICE_MAP},
            dtype=dtype,
            device_map=QWEN3_ASR_DEVICE_MAP,
            max_inference_batch_size=int(execution_identity["batch_size"]),
            max_new_tokens=QWEN3_ASR_MAX_NEW_TOKENS,
            generation_config=generation_config,
        )

        logger.info(
            "ASR model loaded: provider=%s verified_installation=%s",
            self.name,
            verified_request["model"],
        )
        return PreparedModel.bind(model, verified_request, execution_identity)

    def parse_result(
        self, result: Any, layout: ChunkLayout, elapsed_seconds: float
    ) -> ChunkTranscript:
        timestamp_data = getattr(result, "time_stamps", None)
        normalized = adapt_qwen_timestamp_items(
            list(getattr(timestamp_data, "items", []) or [])
        )
        words = tuple(
            TranscriptWord(item.text, item.start, item.end, None) for item in normalized
        )
        duration = layout.sample_count / SAMPLE_RATE
        if words:
            last_word = words[-1]
            overrun = last_word.end - duration
            if (
                last_word.start <= duration < last_word.end
                and overrun <= QWEN3_ASR_END_TIME_TOLERANCE_SECONDS + 1e-9
            ):
                # Qwen3-ASR 强制对齐时间戳粒度是 80ms 会产生误差
                # 这里仅裁剪末词 0.1 秒内的切片尾部越界
                logger.warning(
                    "Clipped Qwen3-ASR final word end from %.3fs to %.3fs "
                    "for chunk_%03d (overrun %.3fs).",
                    last_word.end,
                    duration,
                    layout.index,
                    overrun,
                )
                words = (
                    *words[:-1],
                    TranscriptWord(
                        last_word.text,
                        last_word.start,
                        duration,
                        last_word.probability,
                    ),
                )
        transcript = ChunkTranscript(
            layout.index,
            layout.start_sample,
            layout.end_sample,
            str(getattr(result, "text", "") or ""),
            words,
            {},
            round(elapsed_seconds, 3),
        )
        return transcript

    def transcribe_one(
        self, prepared: Any, samples: np.ndarray, layout: ChunkLayout
    ) -> ChunkTranscript:
        started = time.perf_counter()
        results = unwrap_model(prepared).transcribe(
            [(samples, 16_000)],
            language=self.model_language,
            return_time_stamps=True,
        )
        if len(results) != 1:
            raise RuntimeError(
                "Qwen3-ASR returned an unexpected isolated result count."
            )
        return self.parse_result(results[0], layout, time.perf_counter() - started)

    def transcribe_batch(
        self, prepared: Any, items: list[tuple[np.ndarray, ChunkLayout]]
    ) -> list[ChunkTranscript]:
        started = time.perf_counter()
        results = unwrap_model(prepared).transcribe(
            [(samples, 16_000) for samples, _ in items],
            language=self.model_language,
            return_time_stamps=True,
        )
        if len(results) != len(items):
            raise RuntimeError("Qwen3-ASR returned an unexpected batch result count.")
        elapsed = (time.perf_counter() - started) / max(1, len(items))
        return [
            self.parse_result(result, layout, elapsed)
            for result, (_, layout) in zip(results, items, strict=True)
        ]

    def final_info(self, plan: AsrPipelinePlan, words_present: bool) -> dict[str, Any]:
        request = plan.provider_request
        return {
            "language": self.language,
            "model": request["model"],
            "forced_aligner": request["model"]["aligner_logical_id"],
            "device": request["device"],
            "compute_type": request["compute_type"],
            "batch_size": plan.execution_policy["batch_size"],
            "max_new_tokens": request["max_new_tokens"],
            "word_timestamps": words_present,
        }
