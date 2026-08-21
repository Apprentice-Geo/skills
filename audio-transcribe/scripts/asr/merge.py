from __future__ import annotations

from scripts.asr.alignment import (
    AlignedTranscript,
    AlignmentItem,
    offset_alignment,
    validate_alignment,
)
from scripts.asr.chunking import SAMPLE_RATE
from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript
from scripts.asr.workspace import chunk_key


def merge_chunk_transcripts(
    plan: AsrPipelinePlan,
    results: dict[str, ChunkTranscript],
) -> AlignedTranscript:
    text_parts: list[str] = []
    items: list[AlignmentItem] = []
    for layout in plan.chunks:
        key = chunk_key(layout.index)
        if key not in results:
            raise RuntimeError(f"Missing ASR chunk result: {key}")
        transcript = results[key]
        if not transcript.text and not transcript.words:
            continue
        text_parts.append(transcript.text)
        offset = layout.start_sample / SAMPLE_RATE
        items.extend(offset_alignment(transcript.alignment, offset).items)
    text = " ".join(text_parts)
    alignment = AlignedTranscript(text, tuple(items))
    validate_alignment(
        alignment,
        plan.source.duration,
        chunk_index="merged",
        language=str(plan.provider_request["language"]),
    )
    if not alignment.text.strip() or not alignment.items:
        raise RuntimeError("A complete transcription must contain text and timestamps.")
    return alignment
