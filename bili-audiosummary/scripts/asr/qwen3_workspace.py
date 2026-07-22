from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, cast

from scripts.asr.chunking import SAMPLE_RATE
from scripts.asr.qwen3_alignment import (
    AlignmentContractError,
    AlignmentItem,
    build_sentence_segments,
    validate_alignment_contract,
)
from scripts.asr.qwen3_plan import QWEN3_CACHE_SCHEMA_VERSION
from scripts.utils import read_json, write_json_atomic

QWEN3_SEGMENTATION_VERSION = 2
logger = logging.getLogger(__name__)


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


def _qwen_valid_alignment(
    data: Any,
    max_sample_count: int | None = None,
    *,
    text: str | None = None,
    chunk_index: int | str = "cache",
    language: str = "unknown",
) -> list[AlignmentItem] | None:
    if not isinstance(data, list):
        return None
    items: list[AlignmentItem] = []
    previous = 0.0
    maximum = max_sample_count / SAMPLE_RATE if max_sample_count is not None else None
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            return None
        start, end = raw.get("start"), raw.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
        ):
            return None
        start_value, end_value = float(start), float(end)
        if text is None:
            if (
                not math.isfinite(start_value)
                or not math.isfinite(end_value)
                or start_value < previous
                or end_value < start_value
            ):
                return None
            if maximum is not None and end_value > maximum + 0.001:
                return None
        items.append(AlignmentItem(str(raw["text"]), start_value, end_value))
        previous = end_value
    if text is not None:
        try:
            validate_alignment_contract(
                text,
                items,
                maximum if maximum is not None else previous,
                chunk_index=chunk_index,
                language=language,
            )
        except AlignmentContractError as exc:
            logger.warning("Ignoring invalid Qwen3 alignment cache: %s", exc)
            return None
    return items


def _qwen_load_chunk_results(
    workspace_dir: Path, plan: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    plan_chunks_by_key = {
        _qwen_chunk_key(item["index"]): item for item in plan["chunks"]
    }
    results_dir = _qwen_workspace_paths(workspace_dir)["results"]
    if not results_dir.exists():
        return results
    for path in results_dir.glob("chunk_*.json"):
        data = _qwen_load_json(path)
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != QWEN3_CACHE_SCHEMA_VERSION
            or data.get("plan") != plan
        ):
            continue
        index = data.get("chunk_index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        key = _qwen_chunk_key(index)
        layout = plan_chunks_by_key.get(key)
        if path.stem != key or layout is None:
            continue
        if (
            data.get("start_sample") != layout["start_sample"]
            or data.get("end_sample") != layout["end_sample"]
            or not isinstance(data.get("text"), str)
        ):
            continue
        if (
            _qwen_valid_alignment(
                data.get("word_timestamps"),
                layout["end_sample"] - layout["start_sample"],
                text=data["text"],
                chunk_index=index,
                language=plan["request"]["language"],
            )
            is None
        ):
            continue
        results[key] = data
    return results


def _qwen_progress(
    plan: dict[str, Any],
    results: dict[str, dict[str, Any]],
    failures: dict[str, str] | None = None,
) -> dict[str, Any]:
    failures = failures or {}
    return {
        "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
        "plan": plan,
        "chunks": {
            (key := _qwen_chunk_key(item["index"])): {
                "status": (
                    "succeeded"
                    if key in results
                    else ("failed" if key in failures else "pending")
                ),
                "error": failures.get(key),
                "result_path": f"chunk_results/{key}.json",
            }
            for item in plan["chunks"]
        },
    }


def _qwen_load_merged(
    path: Path,
    plan: dict[str, Any],
    info_factory: Callable[[str, bool], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    data = _qwen_load_json(path)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != QWEN3_CACHE_SCHEMA_VERSION
        or data.get("plan") != plan
    ):
        return None
    segments = data.get("segments")
    if not isinstance(data.get("text"), str):
        return None
    alignment = _qwen_valid_alignment(
        data.get("word_timestamps"),
        plan["source"]["sample_count"],
        text=data["text"],
        chunk_index="merged",
        language=plan["request"]["language"],
    )
    if alignment is None:
        return None
    segments_are_valid = isinstance(segments, list)
    previous_end = 0.0
    alignment_starts = {item.start for item in alignment}
    alignment_ends = {item.end for item in alignment}
    for expected_id, segment in enumerate(
        segments if isinstance(segments, list) else []
    ):
        if (
            not isinstance(segment, dict)
            or isinstance(segment.get("id"), bool)
            or segment.get("id") != expected_id
            or not isinstance(segment.get("text"), str)
            or not segment["text"].strip()
        ):
            segments_are_valid = False
            break
        start, end = segment.get("start"), segment.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or not previous_end <= float(start) <= float(end)
            or float(end) > plan["source"]["sample_count"] / SAMPLE_RATE + 0.001
            or float(start) not in alignment_starts
            or float(end) not in alignment_ends
        ):
            segments_are_valid = False
            break
        previous_end = float(end)
    if segments_are_valid and alignment:
        source_text = "".join(char for char in data["text"] if not char.isspace())
        segment_text = "".join(
            char
            for segment in cast(list[dict[str, Any]], segments)
            for char in segment["text"]
            if not char.isspace()
        )
        segments_are_valid = segment_text == source_text
    if (
        data.get("segmentation_version") != QWEN3_SEGMENTATION_VERSION
        or not segments_are_valid
    ):
        segments = build_sentence_segments(
            data["text"],
            alignment,
            plan["source"]["sample_count"] / SAMPLE_RATE,
            chunk_index="merged",
            language=plan["request"]["language"],
        )
        data["segmentation_version"] = QWEN3_SEGMENTATION_VERSION
        data["word_timestamps"] = [asdict(item) for item in alignment]
        data["segments"] = segments
        write_json_atomic(path, data)
    return info_factory(plan["request"]["language"], bool(alignment)), cast(
        list[dict[str, Any]], segments
    )
