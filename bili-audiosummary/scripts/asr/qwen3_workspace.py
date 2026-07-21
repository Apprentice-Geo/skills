from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

from scripts.asr.chunking import SAMPLE_RATE
from scripts.asr.qwen3_alignment import AlignmentItem
from scripts.asr.qwen3_plan import QWEN3_CACHE_SCHEMA_VERSION
from scripts.utils import read_json


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
    data: Any, max_sample_count: int | None = None
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
    if not isinstance(data.get("text"), str) or not isinstance(segments, list):
        return None
    if (
        _qwen_valid_alignment(
            data.get("word_timestamps"), plan["source"]["sample_count"]
        )
        is None
    ):
        return None
    for segment in segments:
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("id"), int)
            or isinstance(segment.get("id"), bool)
            or not isinstance(segment.get("text"), str)
        ):
            return None
        start, end = segment.get("start"), segment.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or not 0 <= float(start) <= float(end)
        ):
            return None
    return info_factory(
        plan["request"]["language"], bool(data["word_timestamps"])
    ), segments
