from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.asr.chunking import (
    DEFAULT_PLANNING_PARAMETERS as SAMPLE_PLANNING_PARAMETERS,
)
from scripts.asr.chunking import (
    DEFAULT_VAD_PARAMETERS,
    SAMPLE_RATE,
    ChunkLayout,
    plan_chunks,
    validate_layouts,
)
from scripts.config import (
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ASR_MODEL_DIR,
    QWEN3_DEVICE_MAP,
    QWEN3_DTYPE,
    QWEN3_MAX_INFERENCE_BATCH_SIZE,
    QWEN3_MAX_NEW_TOKENS,
)
from scripts.utils import path_to_posix

QWEN3_CACHE_SCHEMA_VERSION = 3
QWEN3_LANGUAGE_NAMES = {"en": "English", "zh": "Chinese"}


def _qwen_language_name(language: str) -> str:
    try:
        return QWEN3_LANGUAGE_NAMES[language.lower()]
    except KeyError as exc:
        supported = ", ".join(sorted(QWEN3_LANGUAGE_NAMES))
        raise ValueError(
            f"Unsupported Qwen3 language: {language}. Supported: {supported}"
        ) from exc


def _qwen_request_identity(language: str) -> dict[str, Any]:
    return {
        "language": language,
        "model_language": _qwen_language_name(language),
        "model": path_to_posix(QWEN3_ASR_MODEL_DIR),
        "forced_aligner": path_to_posix(QWEN3_ALIGNER_MODEL_DIR),
        "device": QWEN3_DEVICE_MAP,
        "compute_type": QWEN3_DTYPE,
        "batch_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
        "max_new_tokens": QWEN3_MAX_NEW_TOKENS,
        "count_strategy": "full",
    }


def _qwen_source_identity(
    audio_path: Path, sample_count: int | None = None
) -> dict[str, Any]:
    stat = audio_path.stat()
    source = {
        "path": path_to_posix(audio_path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    if sample_count is not None:
        source.update(sample_count=int(sample_count), sample_rate=SAMPLE_RATE)
    return source


def _qwen_validate_plan(
    plan: Any,
    audio_path: Path,
    language: str,
) -> dict[str, Any] | None:
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != QWEN3_CACHE_SCHEMA_VERSION
    ):
        return None
    source = plan.get("source")
    if not isinstance(source, dict):
        return None
    current = _qwen_source_identity(audio_path)
    if any(source.get(key) != value for key, value in current.items()):
        return None
    if source.get("sample_rate") != SAMPLE_RATE or not isinstance(
        source.get("sample_count"), int
    ):
        return None
    if plan.get("request") != _qwen_request_identity(language):
        return None
    if plan.get("vad_parameters") != asdict(DEFAULT_VAD_PARAMETERS):
        return None
    if plan.get("planning_parameters") != asdict(SAMPLE_PLANNING_PARAMETERS):
        return None
    if (
        plan.get("group_size") != QWEN3_MAX_INFERENCE_BATCH_SIZE
        or plan.get("count_strategy") != "full"
    ):
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
