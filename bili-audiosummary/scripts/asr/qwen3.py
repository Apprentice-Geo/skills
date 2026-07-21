from __future__ import annotations

import json
import math
import os
import unicodedata
from dataclasses import asdict, dataclass
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
from scripts.asr.chunking import (
    DEFAULT_PLANNING_PARAMETERS as SAMPLE_PLANNING_PARAMETERS,
    SAMPLE_RATE,
    ChunkLayout,
    decode_normalized_audio,
    detect_speech_samples,
    plan_chunks,
    validate_layouts,
)
from scripts.asr.parallel.plan import DEFAULT_VAD_PARAMETERS
from scripts.process_logging import LoggingSession, get_logger, terminal_info
from scripts.utils import ensure_dir, path_to_posix, read_json, write_json_atomic


STRONG_PUNCTUATION = set("。.!！？?")
WEAK_PUNCTUATION = set("，,；;")
MIN_SEGMENT_SECONDS = 3.0
QWEN3_CACHE_SCHEMA_VERSION = 2
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


# Schema 2 chunked runner.  The earlier Schema 1 helper functions above remain
# importable only so older callers receive a clean schema mismatch; production
# dispatch uses the workspace-based implementation below.
def _qwen_request_identity(language: str) -> dict[str, Any]:
    return {
        "language": language,
        "model": path_to_posix(QWEN3_ASR_MODEL_DIR),
        "forced_aligner": path_to_posix(QWEN3_ALIGNER_MODEL_DIR),
        "device": QWEN3_DEVICE_MAP,
        "compute_type": QWEN3_DTYPE,
        "batch_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
        "max_new_tokens": QWEN3_MAX_NEW_TOKENS,
        "count_strategy": "full",
    }


def _qwen_source_identity(audio_path: Path, sample_count: int | None = None) -> dict[str, Any]:
    stat = audio_path.stat()
    source = {
        "path": path_to_posix(audio_path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    if sample_count is not None:
        source.update(sample_count=int(sample_count), sample_rate=SAMPLE_RATE)
    return source


def _qwen_workspace_paths(workspace_dir: Path) -> dict[str, Path]:
    return {
        "plan": workspace_dir / "asr_plan.json",
        "progress": workspace_dir / "progress.json",
        "results": workspace_dir / "chunk_results",
        "merged": workspace_dir / "result.json",
        "vad": workspace_dir / "vad_result.json",
    }


def _qwen_chunk_key(index: int) -> str:
    return f"chunk_{index:03d}"


def _qwen_load_json(path: Path) -> Any | None:
    try:
        return read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _qwen_validate_plan(
    plan: Any,
    audio_path: Path,
    language: str,
) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or plan.get("schema_version") != QWEN3_CACHE_SCHEMA_VERSION:
        return None
    source = plan.get("source")
    if not isinstance(source, dict):
        return None
    current = _qwen_source_identity(audio_path)
    if any(source.get(key) != value for key, value in current.items()):
        return None
    if source.get("sample_rate") != SAMPLE_RATE or not isinstance(source.get("sample_count"), int):
        return None
    if plan.get("request") != _qwen_request_identity(language):
        return None
    if plan.get("group_size") != QWEN3_MAX_INFERENCE_BATCH_SIZE or plan.get("count_strategy") != "full":
        return None
    raw_chunks = plan.get("chunks")
    if not isinstance(raw_chunks, list):
        return None
    try:
        layouts = [ChunkLayout(**item) for item in raw_chunks]
        validate_layouts(layouts, source["sample_count"], SAMPLE_PLANNING_PARAMETERS)
    except (TypeError, ValueError):
        return None
    return plan


def _qwen_build_plan(
    audio_path: Path,
    language: str,
    sample_count: int,
    speech_intervals: list[tuple[int, int]],
) -> dict[str, Any]:
    layouts = plan_chunks(
        sample_count,
        speech_intervals,
        group_size=QWEN3_MAX_INFERENCE_BATCH_SIZE,
        count_strategy="full",
        parameters=SAMPLE_PLANNING_PARAMETERS,
    )
    return {
        "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
        "source": _qwen_source_identity(audio_path, sample_count),
        "request": _qwen_request_identity(language),
        "vad_parameters": asdict(DEFAULT_VAD_PARAMETERS),
        "planning_parameters": asdict(SAMPLE_PLANNING_PARAMETERS),
        "count_strategy": "full",
        "group_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
        "chunks": [asdict(item) for item in layouts],
    }


def _qwen_valid_alignment(data: Any, max_sample_count: int | None = None) -> list[AlignmentItem] | None:
    if not isinstance(data, list):
        return None
    items: list[AlignmentItem] = []
    previous = 0.0
    maximum = max_sample_count / SAMPLE_RATE if max_sample_count is not None else None
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            return None
        start, end = raw.get("start"), raw.get("end")
        if isinstance(start, bool) or not isinstance(start, (int, float)) or isinstance(end, bool) or not isinstance(end, (int, float)):
            return None
        start_value, end_value = float(start), float(end)
        if not math.isfinite(start_value) or not math.isfinite(end_value) or start_value < previous or end_value < start_value:
            return None
        if maximum is not None and end_value > maximum + 0.001:
            return None
        items.append(AlignmentItem(str(raw["text"]), start_value, end_value))
        previous = end_value
    return items


def _qwen_load_chunk_results(workspace_dir: Path, plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    plan_chunks_by_key = {_qwen_chunk_key(item["index"]): item for item in plan["chunks"]}
    results_dir = _qwen_workspace_paths(workspace_dir)["results"]
    if not results_dir.exists():
        return results
    for path in results_dir.glob("chunk_*.json"):
        data = _qwen_load_json(path)
        if not isinstance(data, dict) or data.get("schema_version") != QWEN3_CACHE_SCHEMA_VERSION or data.get("plan") != plan:
            continue
        index = data.get("chunk_index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        key = _qwen_chunk_key(index)
        layout = plan_chunks_by_key.get(key)
        if path.stem != key or layout is None:
            continue
        if data.get("start_sample") != layout["start_sample"] or data.get("end_sample") != layout["end_sample"] or not isinstance(data.get("text"), str):
            continue
        if _qwen_valid_alignment(data.get("word_timestamps"), layout["end_sample"] - layout["start_sample"]) is None:
            continue
        results[key] = data
    return results


def _qwen_progress(plan: dict[str, Any], results: dict[str, dict[str, Any]], failures: dict[str, str] | None = None) -> dict[str, Any]:
    failures = failures or {}
    return {
        "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
        "plan": plan,
        "chunks": {
            (key := _qwen_chunk_key(item["index"])): {
                "status": "succeeded" if key in results else ("failed" if key in failures else "pending"),
                "error": failures.get(key),
                "result_path": f"chunk_results/{key}.json",
            }
            for item in plan["chunks"]
        },
    }


def _qwen_load_merged(path: Path, plan: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    data = _qwen_load_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != QWEN3_CACHE_SCHEMA_VERSION or data.get("plan") != plan:
        return None
    segments = data.get("segments")
    if not isinstance(data.get("text"), str) or not isinstance(segments, list):
        return None
    if _qwen_valid_alignment(data.get("word_timestamps"), plan["source"]["sample_count"]) is None:
        return None
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("id"), int) or isinstance(segment.get("id"), bool) or not isinstance(segment.get("text"), str):
            return None
        start, end = segment.get("start"), segment.get("end")
        if isinstance(start, bool) or not isinstance(start, (int, float)) or isinstance(end, bool) or not isinstance(end, (int, float)) or not 0 <= float(start) <= float(end):
            return None
    return _qwen_info(plan["request"]["language"], bool(data["word_timestamps"])), segments


def _load_qwen_model() -> Any:
    try:
        import torch
        from transformers import GenerationConfig
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
    dtype = getattr(torch, QWEN3_DTYPE)
    generation_config = GenerationConfig.from_pretrained(asr_model, temperature=None)
    return Qwen3ASRModel.from_pretrained(
        asr_model,
        forced_aligner=path_to_posix(QWEN3_ALIGNER_MODEL_DIR),
        forced_aligner_kwargs={"dtype": dtype, "device_map": QWEN3_DEVICE_MAP},
        dtype=dtype,
        device_map=QWEN3_DEVICE_MAP,
        max_inference_batch_size=QWEN3_MAX_INFERENCE_BATCH_SIZE,
        max_new_tokens=QWEN3_MAX_NEW_TOKENS,
        generation_config=generation_config,
    )


def _qwen_result_payload(result: Any, plan: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    timestamp_data = getattr(result, "time_stamps", None)
    alignment = normalize_alignment_items(list(getattr(timestamp_data, "items", []) or []))
    return {
        "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
        "plan": plan,
        "chunk_index": layout["index"],
        "start_sample": layout["start_sample"],
        "end_sample": layout["end_sample"],
        "text": str(getattr(result, "text", "") or "").strip(),
        "word_timestamps": [asdict(item) for item in alignment],
    }


def _qwen_merge(plan: dict[str, Any], results: dict[str, dict[str, Any]]) -> tuple[str, list[AlignmentItem], list[dict[str, Any]]]:
    text_parts = []
    global_items = []
    for layout in plan["chunks"]:
        item = results[_qwen_chunk_key(layout["index"])]
        text_parts.append(item["text"])
        offset = layout["start_sample"] / SAMPLE_RATE
        for word in _qwen_valid_alignment(item["word_timestamps"]) or []:
            global_items.append(AlignmentItem(word.text, round(offset + word.start, 3), round(offset + word.end, 3)))
    text = "".join(text_parts)
    duration = plan["source"]["sample_count"] / SAMPLE_RATE
    return text, global_items, build_sentence_segments(text, global_items, duration)


def transcribe_with_qwen3(
    audio_path: Path,
    language: str,
    workspace_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paths = _qwen_workspace_paths(workspace_dir)
    cached_plan = _qwen_validate_plan(_qwen_load_json(paths["plan"]), audio_path, language)
    if cached_plan is not None:
        merged = _qwen_load_merged(paths["merged"], cached_plan)
        if merged is not None:
            terminal_info(logger, "[Transcribe] Qwen3 cache: complete; skipped audio decode, CUDA check, and model load")
            return merged

    audio = decode_normalized_audio(audio_path)
    if cached_plan is None or cached_plan["source"]["sample_count"] != audio.sample_count:
        speech = detect_speech_samples(audio, DEFAULT_VAD_PARAMETERS)
        plan = _qwen_build_plan(audio_path, language, audio.sample_count, speech)
        ensure_dir(workspace_dir)
        write_json_atomic(paths["plan"], plan)
        write_json_atomic(
            paths["vad"],
            {
                "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
                "source": plan["source"],
                "parameters": asdict(DEFAULT_VAD_PARAMETERS),
                "speech_intervals": [{"start_sample": start, "end_sample": end} for start, end in speech],
            },
        )
    else:
        plan = cached_plan
    results = _qwen_load_chunk_results(workspace_dir, plan)
    pending = [item for item in plan["chunks"] if _qwen_chunk_key(item["index"]) not in results]
    if not pending:
        text, alignment, segments = _qwen_merge(plan, results)
        write_json_atomic(paths["merged"], {"schema_version": QWEN3_CACHE_SCHEMA_VERSION, "plan": plan, "text": text, "word_timestamps": [asdict(item) for item in alignment], "segments": segments})
        return _qwen_info(language, bool(alignment)), segments

    model = _load_qwen_model()
    failures: dict[str, str] = {}

    def cache(layout: dict[str, Any], result: Any) -> None:
        payload = _qwen_result_payload(result, plan, layout)
        key = _qwen_chunk_key(layout["index"])
        write_json_atomic(paths["results"] / f"{key}.json", payload)
        results[key] = payload
        write_json_atomic(paths["progress"], _qwen_progress(plan, results, failures))

    write_json_atomic(paths["progress"], _qwen_progress(plan, results))
    for offset in range(0, len(pending), QWEN3_MAX_INFERENCE_BATCH_SIZE):
        batch = pending[offset : offset + QWEN3_MAX_INFERENCE_BATCH_SIZE]
        inputs = [(audio.samples[item["start_sample"] : item["end_sample"]], SAMPLE_RATE) for item in batch]
        try:
            batch_results = model.transcribe(inputs, return_time_stamps=True)
            if len(batch_results) != len(batch):
                raise RuntimeError("Qwen3 returned an unexpected batch result count.")
            for layout, result in zip(batch, batch_results):
                cache(layout, result)
        except Exception:
            logger.warning("Qwen3 batch failed; isolating each chunk", exc_info=True)
            for layout, input_item in zip(batch, inputs):
                key = _qwen_chunk_key(layout["index"])
                try:
                    isolated = model.transcribe([input_item], return_time_stamps=True)
                    if len(isolated) != 1:
                        raise RuntimeError("Qwen3 returned an unexpected isolated result count.")
                    cache(layout, isolated[0])
                except Exception as exc:
                    failures[key] = str(exc)
                    write_json_atomic(paths["progress"], _qwen_progress(plan, results, failures))
    if failures:
        raise RuntimeError(f"Qwen3 chunks failed after isolation: {', '.join(sorted(failures))}")
    text, alignment, segments = _qwen_merge(plan, results)
    write_json_atomic(paths["merged"], {"schema_version": QWEN3_CACHE_SCHEMA_VERSION, "plan": plan, "text": text, "word_timestamps": [asdict(item) for item in alignment], "segments": segments})
    return _qwen_info(language, bool(alignment)), segments
