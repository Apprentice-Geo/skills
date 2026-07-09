from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.asr.parallel.plan import SCHEMA_VERSION, ParallelAsrPlan
from scripts.asr.parallel.state import chunk_key
from scripts.utils import write_json


def write_metrics(
    path: Path,
    plan: ParallelAsrPlan,
    total_elapsed_seconds: float,
    chunk_results: dict[str, dict[str, Any]],
    failed_chunks: list[str] | None = None,
    macro_elapsed_seconds: list[dict[str, Any]] | None = None,
    segment_count: int | None = None,
) -> dict[str, Any]:
    sorted_results = [
        chunk_results[key]
        for key in sorted(chunk_results, key=lambda value: (chunk_results[value]["macro_index"], chunk_results[value]["chunk_index"]))
    ]
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "total_elapsed_seconds": round(float(total_elapsed_seconds), 3),
        "macro_elapsed_seconds": macro_elapsed_seconds or [],
        "chunk_elapsed_seconds": [
            {
                "macro_index": result["macro_index"],
                "chunk_index": result["chunk_index"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
            for result in sorted_results
        ],
        "task_workers": plan.task_workers,
        "model_workers": plan.model_workers,
        "cpu_threads": plan.cpu_threads,
        "chunk_count": len(plan.asr_chunks),
        "segment_count": segment_count
        if segment_count is not None
        else sum(len(result.get("segments", [])) for result in sorted_results),
        "failed_chunks": failed_chunks or [],
    }
    write_json(path, metrics)
    return metrics


def build_macro_elapsed_from_results(
    plan: ParallelAsrPlan,
    chunk_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    macro_elapsed: list[dict[str, Any]] = []
    for macro in plan.macro_chunks:
        elapsed_values = [
            float(chunk_results[chunk_key(chunk)]["elapsed_seconds"])
            for chunk in macro.chunks
            if chunk_key(chunk) in chunk_results
        ]
        macro_elapsed.append(
            {
                "macro_index": macro.index,
                "elapsed_seconds": round(max(elapsed_values), 3)
                if elapsed_values
                else 0.0,
                "chunk_count": len(macro.chunks),
            }
        )
    return macro_elapsed
