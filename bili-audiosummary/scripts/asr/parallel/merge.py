from __future__ import annotations

from typing import Any

from scripts.asr.common import is_chinese_language
from scripts.asr.parallel.plan import ParallelAsrPlan
from scripts.asr.parallel.state import chunk_key


def _time_ranges_overlap(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    return max(float(left["start"]), float(right["start"])) < min(
        float(left["end"]),
        float(right["end"]),
    )


def merge_chunk_results(
    plan: ParallelAsrPlan,
    chunk_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for chunk in sorted(plan.chunks, key=lambda item: item.index):
        key = chunk_key(chunk)
        if key not in chunk_results:
            raise RuntimeError(f"Missing ASR chunk result: {key}")
        for segment in chunk_results[key].get("segments", []):
            local_start = float(segment["start"])
            local_end = float(segment["end"])
            if local_end < local_start:
                raise RuntimeError("Merged ASR segment end is earlier than start.")
            segments.append(
                {
                    **segment,
                    "id": 0,
                    "start": round(chunk.start + local_start, 3),
                    "end": round(chunk.start + local_end, 3),
                    "_chunk_index": chunk.index,
                }
            )

    segments.sort(
        key=lambda segment: (
            int(segment["_chunk_index"]),
            float(segment["start"]),
            float(segment["end"]),
        )
    )

    merged: list[dict[str, Any]] = []
    separator = "" if is_chinese_language(plan.language) else " "
    for segment in segments:
        if merged and _time_ranges_overlap(merged[-1], segment):
            previous = merged[-1]
            previous["start"] = min(float(previous["start"]), float(segment["start"]))
            previous["end"] = max(float(previous["end"]), float(segment["end"]))
            left_text = str(previous.get("text") or "")
            right_text = str(segment.get("text") or "")
            previous["text"] = (
                left_text + right_text
                if not separator
                else " ".join(
                    text.strip() for text in (left_text, right_text) if text.strip()
                )
            )
            continue
        merged.append(segment)

    for index, segment in enumerate(merged):
        segment["id"] = index
        del segment["_chunk_index"]
    return merged
