from __future__ import annotations

from typing import Any

from scripts.asr.alignment import (
    TranscriptWord,
    build_sentence_segments,
    validate_alignment_contract,
)
from scripts.asr.chunking import SAMPLE_RATE
from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript
from scripts.asr.workspace import chunk_key


def merge_chunk_transcripts(
    plan: AsrPipelinePlan,
    results: dict[str, ChunkTranscript],
) -> tuple[str, list[TranscriptWord], list[dict[str, Any]]]:
    text_parts: list[str] = []
    words: list[TranscriptWord] = []
    for layout in plan.chunks:
        key = chunk_key(layout.index)
        if key not in results:
            raise RuntimeError(f"Missing ASR chunk result: {key}")
        transcript = results[key]
        transcript.validate(language=str(plan.provider_request["language"]))
        text_parts.append(transcript.text)
        # 全局时间戳修正
        offset = layout.start_sample / SAMPLE_RATE
        words.extend(
            TranscriptWord(
                word.text,
                round(offset + word.start, 3),
                round(offset + word.end, 3),
                word.probability,
            )
            for word in transcript.words
        )
    text = " ".join(text_parts)
    duration = plan.source.duration
    validate_alignment_contract(
        text,
        words,
        duration,
        chunk_index="merged",
        language=str(plan.provider_request["language"]),
    )
    segments = build_sentence_segments(
        text,
        words,
        duration,
        chunk_index="merged",
        language=str(plan.provider_request["language"]),
    )
    return text, words, segments
