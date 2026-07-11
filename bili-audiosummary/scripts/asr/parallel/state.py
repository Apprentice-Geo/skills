from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.asr.parallel.plan import (
    SCHEMA_VERSION,
    AsrChunkPlan,
    AsrSourceAudio,
    ParallelAsrPlan,
    plan_from_dict,
    plan_to_dict,
)
from scripts.utils import ensure_dir, read_json


MAX_CHUNK_RETRIES = 1
PROGRESS_STATES = {"pending", "running", "succeeded", "failed"}


def workspace_paths(workspace_dir: Path) -> dict[str, Path]:
    return {
        "root": workspace_dir,
        "plan": workspace_dir / "asr_plan.json",
        "progress": workspace_dir / "progress.json",
        "metrics": workspace_dir / "metrics.json",
        "chunks": workspace_dir / "chunks",
        "chunk_results": workspace_dir / "chunk_results",
        "merged_transcript": workspace_dir / "merged_transcript.json",
    }


def write_plan(path: Path, plan: ParallelAsrPlan) -> None:
    _write_json_atomic(path, plan_to_dict(plan))


def load_plan(path: Path) -> ParallelAsrPlan:
    return plan_from_dict(read_json(path))


def source_matches(plan: ParallelAsrPlan, source_audio: AsrSourceAudio) -> bool:
    return plan.source_audio == source_audio


def chunk_key(chunk: AsrChunkPlan | dict[str, Any]) -> str:
    macro_index = chunk.macro_index if isinstance(chunk, AsrChunkPlan) else int(chunk["macro_index"])
    chunk_index = chunk.chunk_index if isinstance(chunk, AsrChunkPlan) else int(chunk["chunk_index"])
    return f"macro_{macro_index:03d}_chunk_{chunk_index:03d}"


def chunk_result_path(workspace_dir: Path, chunk: AsrChunkPlan) -> Path:
    return workspace_dir / "chunk_results" / f"{chunk_key(chunk)}.json"


def initial_progress(plan: ParallelAsrPlan) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "chunks": {
            chunk_key(chunk): {
                "status": "pending",
                "retry_count": 0,
                "error": None,
                "result_path": f"chunk_results/{chunk_key(chunk)}.json",
            }
            for chunk in plan.asr_chunks
        },
    }


def write_progress(path: Path, progress: dict[str, Any]) -> None:
    _write_json_atomic(path, progress)


def load_progress(path: Path) -> dict[str, Any]:
    progress = read_json(path)
    if not isinstance(progress, dict):
        raise ValueError("Invalid ASR progress: root must be an object")
    if progress.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            "Invalid ASR progress schema_version: "
            f"expected {SCHEMA_VERSION}, got {progress.get('schema_version')}"
        )
    chunks = progress.get("chunks")
    if not isinstance(chunks, dict):
        raise ValueError("Invalid ASR progress: chunks must be an object")
    for key, item in chunks.items():
        if not isinstance(item, dict):
            raise ValueError(f"Invalid ASR progress chunk {key}: item must be an object")
        status = item.get("status")
        if status not in PROGRESS_STATES:
            raise ValueError(f"Invalid ASR progress chunk {key} status: {status}")
        retry_count = item.get("retry_count")
        if (
            isinstance(retry_count, bool)
            or not isinstance(retry_count, int)
            or not 0 <= retry_count <= MAX_CHUNK_RETRIES
        ):
            raise ValueError(
                f"Invalid ASR progress chunk {key} retry_count: {retry_count}"
            )
    return progress


def prepare_progress_for_resume(
    plan: ParallelAsrPlan,
    progress: dict[str, Any] | None,
    valid_result_keys: set[str],
) -> dict[str, Any]:
    progress = initial_progress(plan)
    for chunk in plan.asr_chunks:
        key = chunk_key(chunk)
        if key in valid_result_keys:
            progress["chunks"][key]["status"] = "succeeded"
    return progress


def failed_chunks_blocking_merge(progress: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    for key, item in progress.get("chunks", {}).items():
        if item.get("status") == "failed" and int(item.get("retry_count", 0)) >= MAX_CHUNK_RETRIES:
            failed.append(key)
    return failed


def _valid_chunk_result(data: dict[str, Any], plan: ParallelAsrPlan) -> bool:
    required = {
        "schema_version",
        "macro_index",
        "chunk_index",
        "start",
        "duration",
        "source_start",
        "source_duration",
        "overlap",
        "source",
        "plan",
        "model",
        "elapsed_seconds",
        "segments",
    }
    if not required.issubset(data):
        return False
    if data["schema_version"] != SCHEMA_VERSION:
        return False
    if data["source"] != asdict(plan.source_audio):
        return False
    if data["plan"] != asdict(plan):
        return False
    try:
        key = chunk_key(data)
    except (KeyError, TypeError, ValueError):
        return False

    chunks = {chunk_key(chunk): chunk for chunk in plan.asr_chunks}
    chunk = chunks.get(key)
    if chunk is None:
        return False
    macro = plan.macro_chunks[chunk.macro_index]
    expected_model = {
        "path": plan.model,
        "language": plan.language,
        "beam_size": plan.beam_size,
        "device": plan.device,
        "compute_type": plan.compute_type,
        "cpu_threads": macro.cpu_threads,
        "model_workers": macro.model_workers,
    }
    return (
        data["start"] == chunk.start
        and data["duration"] == chunk.duration
        and data["source_start"] == chunk.source_start
        and data["source_duration"] == chunk.source_duration
        and data["overlap"]
        == {"left": chunk.left_overlap, "right": chunk.right_overlap}
        and data["model"] == expected_model
        and isinstance(data["segments"], list)
    )


def load_valid_chunk_results(workspace_dir: Path, plan: ParallelAsrPlan) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    results_dir = workspace_dir / "chunk_results"
    if not results_dir.exists():
        return results
    for result_path in results_dir.glob("macro_*_chunk_*.json"):
        try:
            data = read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        if _valid_chunk_result(data, plan):
            key = chunk_key(data)
            results[key] = data
    return results


def _write_json_atomic(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def write_chunk_result_atomic(path: Path, data: dict[str, Any]) -> None:
    _write_json_atomic(path, data)
