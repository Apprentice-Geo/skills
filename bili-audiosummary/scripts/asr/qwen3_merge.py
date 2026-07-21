from __future__ import annotations

from typing import Any

from scripts.asr.chunking import SAMPLE_RATE
from scripts.asr.qwen3_alignment import AlignmentItem, build_sentence_segments
from scripts.asr.qwen3_workspace import _qwen_chunk_key, _qwen_valid_alignment


def _qwen_merge(
    plan: dict[str, Any], results: dict[str, dict[str, Any]]
) -> tuple[str, list[AlignmentItem], list[dict[str, Any]]]:
    text_parts = []
    global_items = []
    for layout in plan["chunks"]:
        item = results[_qwen_chunk_key(layout["index"])]
        text_parts.append(item["text"].strip())
        offset = layout["start_sample"] / SAMPLE_RATE
        for word in _qwen_valid_alignment(item["word_timestamps"]) or []:
            global_items.append(
                AlignmentItem(
                    word.text,
                    round(offset + word.start, 3),
                    round(offset + word.end, 3),
                )
            )
    text = " ".join(part for part in text_parts if part)
    duration = plan["source"]["sample_count"] / SAMPLE_RATE
    return text, global_items, build_sentence_segments(text, global_items, duration)
