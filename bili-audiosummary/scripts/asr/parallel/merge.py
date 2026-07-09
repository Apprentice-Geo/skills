from __future__ import annotations

from typing import Any

from scripts.asr.parallel.plan import AsrChunkPlan, MacroChunkPlan, ParallelAsrPlan
from scripts.asr.parallel.state import chunk_key


def _chunk_by_key(plan: ParallelAsrPlan) -> dict[str, AsrChunkPlan]:
    return {chunk_key(chunk): chunk for chunk in plan.asr_chunks}


def _macro_by_index(plan: ParallelAsrPlan) -> dict[int, MacroChunkPlan]:
    return {macro.index: macro for macro in plan.macro_chunks}


def merge_chunk_results(
    plan: ParallelAsrPlan,
    chunk_results: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result_map = (
        {chunk_key(result): result for result in chunk_results}
        if isinstance(chunk_results, list)
        else chunk_results
    )
    macros = _macro_by_index(plan)
    chunks = _chunk_by_key(plan)
    merged: list[dict[str, Any]] = []
    previous_start = 0.0

    for key in sorted(chunks, key=lambda value: (chunks[value].macro_index, chunks[value].chunk_index)):
        if key not in result_map:
            raise RuntimeError(f"Missing ASR chunk result: {key}")
        chunk = chunks[key]
        macro = macros[chunk.macro_index]
        result = result_map[key]
        trusted_start = macro.start + chunk.start
        trusted_end = trusted_start + chunk.duration
        offset = macro.start + chunk.source_start
        for segment in result.get("segments", []):
            global_start = round(float(segment["start"]) + offset, 3)
            global_end = round(float(segment["end"]) + offset, 3)
            midpoint = (global_start + global_end) / 2
            if midpoint < trusted_start or midpoint > trusted_end:
                continue
            merged.append(
                {
                    **segment,
                    "id": 0,
                    "start": global_start,
                    "end": global_end,
                    "_macro_index": chunk.macro_index,
                    "_chunk_index": chunk.chunk_index,
                }
            )

    merged.sort(
        key=lambda segment: (
            int(segment["_macro_index"]),
            int(segment["_chunk_index"]),
            float(segment["start"]),
            float(segment["end"]),
        )
    )
    for index, segment in enumerate(merged):
        start = float(segment["start"])
        if index > 0 and start < previous_start:
            raise RuntimeError("Merged ASR timestamps are not monotonic.")
        if float(segment["end"]) < start:
            raise RuntimeError("Merged ASR segment end is earlier than start.")
        previous_start = start
        segment["id"] = index
        del segment["_macro_index"]
        del segment["_chunk_index"]
    return merged
