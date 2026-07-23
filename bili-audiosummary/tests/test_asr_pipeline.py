from __future__ import annotations

from dataclasses import asdict

import pytest

from scripts.asr.alignment import AlignmentContractError, TranscriptWord
from scripts.asr.pipeline_types import ASR_PIPELINE_SCHEMA_VERSION, ChunkTranscript


def test_chunk_transcript_serializes_probability() -> None:
    transcript = ChunkTranscript(
        chunk_index=2,
        start_sample=16_000,
        end_sample=32_000,
        text="hello",
        words=(TranscriptWord("hello", 0.0, 0.5, 0.9),),
        provider_metadata={"language": "en"},
        elapsed_seconds=1.25,
    )

    assert ASR_PIPELINE_SCHEMA_VERSION == 1
    assert asdict(transcript)["words"][0]["probability"] == 0.9


def test_alignment_rejects_word_outside_chunk() -> None:
    with pytest.raises(AlignmentContractError, match="invalid token time"):
        ChunkTranscript(
            chunk_index=0,
            start_sample=0,
            end_sample=16_000,
            text="hello",
            words=(TranscriptWord("hello", 0.0, 1.1, None),),
            provider_metadata={},
            elapsed_seconds=0.1,
        ).validate(language="en")
