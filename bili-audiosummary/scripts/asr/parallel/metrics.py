from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.asr.parallel.plan import SCHEMA_VERSION, ParallelAsrPlan
from scripts.utils import write_json


def write_metrics(
    path: Path,
    plan: ParallelAsrPlan,
    total_elapsed_seconds: float,
    chunk_results: dict[str, dict[str, Any]],
    segment_count: int,
    failed_chunks: list[str] | None = None,
) -> dict[str, Any]:
    sorted_results = [
        chunk_results[key]
        for key in sorted(
            chunk_results,
            key=lambda value: int(chunk_results[value]["chunk_index"]),
        )
    ]
    speech_loads = [chunk.estimated_speech_duration for chunk in plan.chunks]
    total_speech = sum(speech_loads)
    chunk_count = len(speech_loads)
    speech_load_msre = (
        sum((chunk_count * load - total_speech) ** 2 for load in speech_loads)
        / (chunk_count * total_speech * total_speech)
        if total_speech
        else 0.0
    )
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "total_elapsed_seconds": round(float(total_elapsed_seconds), 3),
        "chunk_elapsed_seconds": [
            {
                "chunk_index": result["chunk_index"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
            for result in sorted_results
        ],
        "num_workers": plan.num_workers,
        "cpu_threads": plan.cpu_threads,
        "chunk_count": len(plan.chunks),
        "batch_count": len(plan.chunks) // plan.num_workers,
        "hard_cut_count": sum(chunk.end_boundary == "hard" for chunk in plan.chunks),
        "chunk_estimated_speech_durations": speech_loads,
        "max_estimated_speech_duration": max(speech_loads, default=0.0),
        "speech_load_msre": speech_load_msre,
        "segment_count": segment_count,
        "failed_chunks": failed_chunks or [],
    }
    write_json(path, metrics)
    return metrics
