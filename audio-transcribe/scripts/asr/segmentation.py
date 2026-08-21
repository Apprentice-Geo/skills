from __future__ import annotations

from typing import Any

from scripts.asr.alignment import (
    AlignedTranscript,
    quantize_timestamp,
    sentence_boundaries,
)


def build_sentence_segments(
    alignment: AlignedTranscript,
    *,
    chunk_index: int | str = "merged",
    language: str = "unknown",
) -> list[dict[str, Any]]:
    """Build sentence segments from an already validated alignment."""
    if not alignment.items:
        return []

    segments: list[dict[str, Any]] = []
    sentence_start_char = 0
    sentence_start_item = 0

    def append_sentence(sentence_boundary: int, end_item: int) -> None:
        nonlocal sentence_start_char, sentence_start_item
        if end_item == sentence_start_item:
            sentence_start_char = sentence_boundary
            return
        sentence_text = alignment.text[sentence_start_char:sentence_boundary].strip()
        if sentence_text:
            segments.append(
                {
                    "id": len(segments),
                    "start": quantize_timestamp(
                        alignment.items[sentence_start_item].start
                    ),
                    "end": quantize_timestamp(alignment.items[end_item - 1].end),
                    "text": sentence_text,
                }
            )
        sentence_start_char = sentence_boundary
        sentence_start_item = end_item

    for boundary in sentence_boundaries(
        alignment,
        chunk_index=chunk_index,
        language=language,
    ):
        append_sentence(*boundary)

    sentence_text = alignment.text[sentence_start_char:].strip()
    if sentence_text:
        segments.append(
            {
                "id": len(segments),
                "start": quantize_timestamp(alignment.items[sentence_start_item].start),
                "end": quantize_timestamp(alignment.items[-1].end),
                "text": sentence_text,
            }
        )
    return segments
