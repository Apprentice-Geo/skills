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
    VadParameters,
    plan_from_dict,
    plan_to_dict,
)
from scripts.utils import ensure_dir, read_json, write_json_atomic

MAX_CHUNK_RETRIES = 1
PROGRESS_STATES = {"pending", "running", "succeeded", "failed"}
VAD_RESULT_SCHEMA_VERSION = 2


def workspace_paths(workspace_dir: Path) -> dict[str, Path]:
    return {
        "root": workspace_dir,
        "plan": workspace_dir / "asr_plan.json",
        "progress": workspace_dir / "progress.json",
        "metrics": workspace_dir / "metrics.json",
        "chunk_results": workspace_dir / "chunk_results",
        "vad_result": workspace_dir / "vad_result.json",
        "merged_transcript": workspace_dir / "merged_transcript.json",
    }


def write_vad_result(
    path: Path,
    source_audio: AsrSourceAudio,
    vad_parameters: VadParameters,
    speech_intervals: list[tuple[int, int]],
) -> None:
    write_json_atomic(
        path,
        {
            "schema_version": VAD_RESULT_SCHEMA_VERSION,
            "source": asdict(source_audio),
            "parameters": asdict(vad_parameters),
            "speech_intervals": [
                {"start_sample": start, "end_sample": end}
                for start, end in speech_intervals
            ],
        },
    )


def load_valid_vad_result(
    path: Path,
    source_audio: AsrSourceAudio,
    vad_parameters: VadParameters,
) -> list[tuple[int, int]] | None:
    try:
        data = read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != VAD_RESULT_SCHEMA_VERSION:
        return None
    if data.get("source") != asdict(source_audio):
        return None
    if data.get("parameters") != asdict(vad_parameters):
        return None
    raw_intervals = data.get("speech_intervals")
    if not isinstance(raw_intervals, list):
        return None

    intervals: list[tuple[int, int]] = []
    previous_end = 0
    for item in raw_intervals:
        if not isinstance(item, dict):
            return None
        start = item.get("start_sample")
        end = item.get("end_sample")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
        ):
            return None
        if (
            start < previous_end
            or end < start
            or end > source_audio.sample_count
        ):
            return None
        intervals.append((start, end))
        previous_end = end
    return intervals


def write_plan(path: Path, plan: ParallelAsrPlan) -> None:
    _write_json_atomic(path, plan_to_dict(plan))


def load_plan(path: Path) -> ParallelAsrPlan:
    return plan_from_dict(read_json(path))


def source_matches(plan: ParallelAsrPlan, source_audio: AsrSourceAudio) -> bool:
    return plan.source_audio == source_audio


def chunk_key(chunk: AsrChunkPlan | dict[str, Any]) -> str:
    index = chunk.index if isinstance(chunk, AsrChunkPlan) else int(chunk["chunk_index"])
    return f"chunk_{index:03d}"


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
            for chunk in plan.chunks
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
    del progress
    resumed = initial_progress(plan)
    for chunk in plan.chunks:
        key = chunk_key(chunk)
        if key in valid_result_keys:
            resumed["chunks"][key]["status"] = "succeeded"
    return resumed


def failed_chunks_blocking_merge(progress: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    for key, item in progress.get("chunks", {}).items():
        if (
            item.get("status") == "failed"
            and int(item.get("retry_count", 0)) >= MAX_CHUNK_RETRIES
        ):
            failed.append(key)
    return failed


def _valid_chunk_result(data: Any, plan: ParallelAsrPlan) -> bool:
    if not isinstance(data, dict):
        return False
    required = {
        "schema_version",
        "chunk_index",
        "start_sample",
        "end_sample",
        "end_boundary",
        "source",
        "plan",
        "model",
        "elapsed_seconds",
        "segments",
    }
    if not required.issubset(data) or data["schema_version"] != SCHEMA_VERSION:
        return False
    if data["source"] != asdict(plan.source_audio):
        return False
    if data["plan"] != plan_to_dict(plan):
        return False
    try:
        key = chunk_key(data)
    except (KeyError, TypeError, ValueError):
        return False

    chunks = {chunk_key(chunk): chunk for chunk in plan.chunks}
    chunk = chunks.get(key)
    if chunk is None:
        return False
    expected_model = {
        "path": plan.model,
        "language": plan.language,
        "beam_size": plan.beam_size,
        "device": plan.device,
        "compute_type": plan.compute_type,
        "cpu_threads": plan.cpu_threads,
        "num_workers": plan.num_workers,
    }
    return (
        data["start_sample"] == chunk.start_sample
        and data["end_sample"] == chunk.end_sample
        and data["end_boundary"] == chunk.end_boundary
        and data["model"] == expected_model
        and isinstance(data["elapsed_seconds"], (int, float))
        and not isinstance(data["elapsed_seconds"], bool)
        and isinstance(data["segments"], list)
    )


def load_valid_chunk_results(
    workspace_dir: Path,
    plan: ParallelAsrPlan,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    results_dir = workspace_dir / "chunk_results"
    if not results_dir.exists():
        return results
    for result_path in results_dir.glob("chunk_*.json"):
        try:
            data = read_json(result_path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if _valid_chunk_result(data, plan):
            key = chunk_key(data)
            if result_path.stem == key:
                results[key] = data
    return results


def _write_json_atomic(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def write_chunk_result_atomic(path: Path, data: dict[str, Any]) -> None:
    _write_json_atomic(path, data)
