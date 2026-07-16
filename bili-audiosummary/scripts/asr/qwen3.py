from __future__ import annotations

import json
import math
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
from scripts.process_logging import LoggingSession, get_logger, terminal_info
from scripts.utils import path_to_posix, read_json, write_json_atomic


STRONG_PUNCTUATION = set("。.!！？?")
WEAK_PUNCTUATION = set("，,；;")
MIN_SEGMENT_SECONDS = 3.0
QWEN3_CACHE_SCHEMA_VERSION = 1
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
    # Unicode 通用类别中 所有标点符号都以 P 开头
    # 此处判断字符是否为标点符号
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


def build_intermediate_payload(
    text: str,
    alignment_items: list[AlignmentItem],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    normalized_text = text.strip()
    if normalized_text:
        payload["text"] = normalized_text
    if alignment_items:
        payload["word_timestamps"] = [
            {
                "text": item.text,
                "start": item.start,
                "end": item.end,
            }
            for item in alignment_items
        ]
    return payload


def build_cache_identity(
    audio_path: Path,
    language: str,
    duration: float | None,
) -> dict[str, Any]:
    stat = audio_path.stat()
    return {
        "source": {
            "path": path_to_posix(audio_path),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "duration": float(duration) if duration is not None else None,
        },
        "request": {
            "language": language,
            "model": path_to_posix(QWEN3_ASR_MODEL_DIR),
            "forced_aligner": path_to_posix(QWEN3_ALIGNER_MODEL_DIR),
            "device": QWEN3_DEVICE_MAP,
            "compute_type": QWEN3_DTYPE,
            "batch_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
            "max_new_tokens": QWEN3_MAX_NEW_TOKENS,
        },
    }


def _load_alignment_items(data: Any) -> list[AlignmentItem] | None:
    if not isinstance(data, list) or not data:
        return None

    alignment_items: list[AlignmentItem] = []
    previous_start = 0.0
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            return None
        text = item.get("text")
        start = item.get("start")
        end = item.get("end")
        if not isinstance(text, str) or not text.strip():
            return None
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
        ):
            return None
        start_value = float(start)
        end_value = float(end)
        if (
            not math.isfinite(start_value)
            or not math.isfinite(end_value)
            or start_value < 0
            or end_value < start_value
            or (index > 0 and start_value < previous_start)
        ):
            return None
        alignment_items.append(
            AlignmentItem(
                text=text,
                start=_to_float(start_value),
                end=_to_float(end_value),
            )
        )
        previous_start = start_value
    return alignment_items


def load_cached_intermediate_result(
    path: Path,
    audio_path: Path,
    language: str,
    duration: float | None,
) -> tuple[str, list[AlignmentItem]] | None:
    try:
        data = read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != QWEN3_CACHE_SCHEMA_VERSION:
        return None
    identity = build_cache_identity(audio_path, language, duration)
    if data.get("source") != identity["source"]:
        return None
    if data.get("request") != identity["request"]:
        return None
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    alignment_items = _load_alignment_items(data.get("word_timestamps"))
    if alignment_items is None:
        return None
    return text.strip(), alignment_items


def write_intermediate_result(
    path: Path,
    audio_path: Path,
    language: str,
    duration: float | None,
    text: str,
    alignment_items: list[AlignmentItem],
) -> None:
    payload = build_intermediate_payload(text, alignment_items)
    if not payload:
        path.unlink(missing_ok=True)
        return
    write_json_atomic(
        path,
        {
            "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
            **build_cache_identity(audio_path, language, duration),
            **payload,
        },
    )


def _qwen_info(language: str, word_timestamps: bool) -> dict[str, Any]:
    return {
        "language": language,
        "model": path_to_posix(QWEN3_ASR_MODEL_DIR),
        "forced_aligner": path_to_posix(QWEN3_ALIGNER_MODEL_DIR),
        "device": QWEN3_DEVICE_MAP,
        "compute_type": QWEN3_DTYPE,
        "batch_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
        "max_new_tokens": QWEN3_MAX_NEW_TOKENS,
        "word_timestamps": word_timestamps,
    }


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
    # 将 Qwen3 的文本和时间戳对齐，生成句子级别的转写结果
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
        # 将剩余的文本作为一个新的段落添加到 segments 中
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
            # 强标点符号表示句子结束，立即刷新当前段落
            flush_segment(strong_end=True)
            continue

        if char in WEAK_PUNCTUATION and current_start is not None and current_end is not None:
            # 弱标点符号表示句子可能结束，但需要检查当前段落的长度是否大于切分阈值
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


def transcribe_with_qwen3(
    audio_path: Path,
    language: str,
    duration: float | None = None,
    intermediate_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if intermediate_path is not None:
        cache_exists = intermediate_path.exists()
        cached_result = (
            load_cached_intermediate_result(
                intermediate_path,
                audio_path,
                language,
                duration,
            )
            if cache_exists
            else None
        )
        if cached_result is not None:
            text, alignment_items = cached_result
            terminal_info(
                logger,
                "[Transcribe] Qwen3 cache: reused asr_qwen3/result.json",
            )
            return (
                _qwen_info(language, word_timestamps=True),
                build_sentence_segments(text, alignment_items, duration),
            )
        terminal_info(
            logger,
            "[Transcribe] Qwen3 cache: %s; %s result.json",
            "invalid" if cache_exists else "missing",
            "regenerating" if cache_exists else "generating",
        )

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
    timestamp_data = getattr(result, "time_stamps", None)
    alignment_items = normalize_alignment_items(
        list(getattr(timestamp_data, "items", []) or [])
    )
    text = str(getattr(result, "text", "") or "").strip()
    if intermediate_path is not None:
        write_intermediate_result(
            intermediate_path,
            audio_path,
            language,
            duration,
            text,
            alignment_items,
        )
    segments = build_sentence_segments(text, alignment_items, duration)
    logger.info(
        "Qwen3 transcription completed: alignment_items=%d segments=%d",
        len(alignment_items),
        len(segments),
    )
    return _qwen_info(language, word_timestamps=bool(alignment_items)), segments
