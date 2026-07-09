from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.config import (
    DEFAULT_HF_ENDPOINT,
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ALIGNER_MODEL_REPO,
    QWEN3_ASR_MODEL_DIR,
    QWEN3_ASR_MODEL_REPO,
    QWEN3_DEVICE_MAP,
    QWEN3_DTYPE,
    QWEN3_MAX_INFERENCE_BATCH_SIZE,
    QWEN3_MAX_NEW_TOKENS,
)
from scripts.process_logging import LoggingSession, get_logger
from scripts.utils import path_to_posix


STRONG_PUNCTUATION = set("。.!！？?")
WEAK_PUNCTUATION = set("，,；;")
MIN_SEGMENT_SECONDS = 3.0
logger = get_logger(__name__)


@dataclass
class AlignmentItem:
    text: str
    start: float
    end: float


@dataclass
class SegmentDraft:
    text: str
    start: float
    end: float
    strong_end: bool

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def consumes_timestamp(char: str) -> bool:
    if char.isspace():
        return False
    return not unicodedata.category(char).startswith("P")


def _to_float(value: Any) -> float:
    return round(float(value), 3)


def consume_alignment_item(alignment_items: list[AlignmentItem], item_index: int) -> tuple[AlignmentItem | None, int]:
    if item_index >= len(alignment_items):
        return None, item_index

    return alignment_items[item_index], item_index + 1


def has_model_weights(model_dir: Path) -> bool:
    return (model_dir / "model.safetensors").exists()


def normalize_alignment_items(items: list[Any]) -> list[AlignmentItem]:
    normalized: list[AlignmentItem] = []
    for item in items:
        text = str(getattr(item, "text", "") or "")
        start = getattr(item, "start_time", None)
        end = getattr(item, "end_time", None)
        if start is None or end is None:
            continue
        normalized.append(AlignmentItem(text=text, start=_to_float(start), end=_to_float(end)))
    return normalized


def _merge_segments(segments: list[SegmentDraft]) -> list[SegmentDraft]:
    merged = list(segments)
    while True:
        changed = False
        index = 0
        while index < len(merged):
            current = merged[index]
            if current.duration >= MIN_SEGMENT_SECONDS:
                index += 1
                continue

            if index + 1 < len(merged) and not current.strong_end:
                nxt = merged[index + 1]
                merged[index] = SegmentDraft(
                    text=f"{current.text}{nxt.text}",
                    start=current.start,
                    end=nxt.end,
                    strong_end=nxt.strong_end,
                )
                del merged[index + 1]
                changed = True
                continue

            if index > 0 and not merged[index - 1].strong_end:
                prev = merged[index - 1]
                merged[index - 1] = SegmentDraft(
                    text=f"{prev.text}{current.text}",
                    start=prev.start,
                    end=current.end,
                    strong_end=current.strong_end,
                )
                del merged[index]
                changed = True
                index -= 1
                continue

            index += 1

        if not changed:
            return merged


def build_sentence_segments(
    text: str,
    alignment_items: list[AlignmentItem],
    duration: float | None = None,
) -> list[dict[str, Any]]:
    segments: list[SegmentDraft] = []
    item_index = 0
    current_chars: list[str] = []
    current_start: float | None = None
    current_end: float | None = None

    def flush_segment(strong_end: bool) -> None:
        nonlocal current_chars, current_start, current_end

        chunk_text = "".join(current_chars).strip()
        if chunk_text and current_start is not None and current_end is not None:
            segments.append(
                SegmentDraft(
                    text=chunk_text,
                    start=_to_float(current_start),
                    end=_to_float(current_end),
                    strong_end=strong_end,
                )
            )

        current_chars = []
        current_start = None
        current_end = None

    def append_remaining_segment(remaining_text: str) -> None:
        nonlocal current_start, current_end

        tail_text = remaining_text.strip()
        if not tail_text:
            return

        start = current_end
        if start is None and alignment_items:
            start = alignment_items[-1].end
        if start is None:
            start = 0.0

        end = duration if duration is not None else start
        if end < start:
            end = start

        segments.append(
            SegmentDraft(
                text=tail_text,
                start=_to_float(start),
                end=_to_float(end),
                strong_end=tail_text[-1] in STRONG_PUNCTUATION,
            )
        )

    for index, char in enumerate(text):
        if consumes_timestamp(char):
            item, item_index = consume_alignment_item(alignment_items, item_index)
            if item is None:
                flush_segment(strong_end=False)
                append_remaining_segment(text[index:])
                break
            if current_start is None:
                current_start = item.start
            current_end = item.end

        current_chars.append(char)

        if char in STRONG_PUNCTUATION:
            flush_segment(strong_end=True)
            continue

        if char in WEAK_PUNCTUATION and current_start is not None and current_end is not None:
            if current_end - current_start >= MIN_SEGMENT_SECONDS:
                flush_segment(strong_end=False)

    else:
        flush_segment(strong_end=False)

    normalized = _merge_segments(segments)
    return [
        {
            "id": index,
            "start": _to_float(segment.start),
            "end": _to_float(segment.end),
            "text": segment.text,
        }
        for index, segment in enumerate(normalized)
    ]


def transcribe_with_qwen3(audio_path: Path, language: str, duration: float | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        import torch
        from transformers import GenerationConfig

        session = LoggingSession.current()
        if session is not None:
            session.capture_logger("transformers")

        from qwen_asr import Qwen3ASRModel
    except ImportError as exc:
        raise RuntimeError(
            "Qwen3 ASR dependencies are not installed. Run "
            r"uv sync --python 3.12 --no-dev --extra qwen3, then "
            r"uv run --no-sync python -m scripts.setup.install_model --model qwen3."
        ) from exc

    if not torch.cuda.is_available():
        raise RuntimeError("Qwen3 ASR requires an available CUDA GPU. Use the default whisper provider on CPU.")

    if not has_model_weights(QWEN3_ASR_MODEL_DIR) or not has_model_weights(QWEN3_ALIGNER_MODEL_DIR):
        raise RuntimeError(
            "Qwen3 local models are missing. Run "
            r"uv run --no-sync python -m scripts.setup.install_model --model qwen3."
        )

    os.environ.setdefault("HF_ENDPOINT", DEFAULT_HF_ENDPOINT)
    asr_model = path_to_posix(QWEN3_ASR_MODEL_DIR)
    aligner_model = path_to_posix(QWEN3_ALIGNER_MODEL_DIR)
    dtype = getattr(torch, QWEN3_DTYPE)
    generation_config = GenerationConfig.from_pretrained(
        asr_model,
        temperature=None,
    )
    logger.info(
        "Loading Qwen3 ASR model=%s aligner=%s device=%s dtype=%s",
        asr_model,
        aligner_model,
        QWEN3_DEVICE_MAP,
        QWEN3_DTYPE,
    )

    asr = Qwen3ASRModel.from_pretrained(
        asr_model,
        forced_aligner=aligner_model,
        forced_aligner_kwargs={"dtype": dtype, "device_map": QWEN3_DEVICE_MAP},
        dtype=dtype,
        device_map=QWEN3_DEVICE_MAP,
        max_inference_batch_size=QWEN3_MAX_INFERENCE_BATCH_SIZE,
        max_new_tokens=QWEN3_MAX_NEW_TOKENS,
        generation_config=generation_config,
    )
    result = asr.transcribe(path_to_posix(audio_path), return_time_stamps=True)[0]
    alignment_items = normalize_alignment_items(list(getattr(result.time_stamps, "items", [])))
    segments = build_sentence_segments(str(result.text or "").strip(), alignment_items, duration)
    logger.info(
        "Qwen3 transcription completed: alignment_items=%d segments=%d",
        len(alignment_items),
        len(segments),
    )
    info = {
        "language": language,
        "model": asr_model,
        "forced_aligner": aligner_model,
        "device": QWEN3_DEVICE_MAP,
        "compute_type": QWEN3_DTYPE,
        "batch_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
        "max_new_tokens": QWEN3_MAX_NEW_TOKENS,
        "word_timestamps": False,
    }
    return info, segments
