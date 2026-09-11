from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

import pytest

from scripts.asr.alignment import (
    ALIGNMENT_POLICY,
    AlignmentContractError,
    TranscriptWord,
)
from scripts.asr.chunking import ChunkLayout, PlanningParameters, VadParameters
from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript, SourceIdentity
from scripts.io_utils import canonical_sha256


def _plan() -> AsrPipelinePlan:
    return AsrPipelinePlan(
        source=SourceIdentity("a" * 64, 10, 16_000),
        provider_request={
            "provider": "fake",
            "language": "en",
            "alignment_policy": dict(ALIGNMENT_POLICY),
        },
        execution_policy={"policy": "serial", "num_workers": 1},
        vad_parameters=VadParameters(),
        planning_parameters=PlanningParameters(1, 16_000),
        chunks=(ChunkLayout(0, 0, 16_000, "audio_end", 8_000),),
    )


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

    assert asdict(transcript)["words"][0]["probability"] == 0.9


def test_plan_id_uses_canonical_payload_without_self_identity() -> None:
    plan = _plan()

    assert plan.plan_id == canonical_sha256(plan.canonical_payload())
    assert plan.to_dict() == {"plan_id": plan.plan_id, **plan.canonical_payload()}
    assert "schema_version" not in plan.to_dict()
    assert AsrPipelinePlan.from_dict(plan.to_dict()) == plan


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(schema_version=2),
        lambda data: data.pop("chunks"),
        lambda data: data.update(plan_id="0" * 64),
        lambda data: data["source"].update(sample_count=True),
        lambda data: data["vad_parameters"].update(threshold="0.35"),
        lambda data: data["planning_parameters"].update(max_chunk_samples=True),
        lambda data: data["chunks"][0].update(index=False),
        lambda data: data["chunks"][0].update(extra="invalid"),
        lambda data: data["execution_policy"].update(load=float("nan")),
    ],
)
def test_plan_loader_rejects_noncanonical_shape_and_values(mutation) -> None:
    data = deepcopy(_plan().to_dict())
    mutation(data)

    with pytest.raises(ValueError, match="Invalid ASR"):
        AsrPipelinePlan.from_dict(data)


def test_alignment_rejects_word_outside_chunk() -> None:
    with pytest.raises(AlignmentContractError, match="invalid timestamp item"):
        ChunkTranscript(
            chunk_index=0,
            start_sample=0,
            end_sample=16_000,
            text="hello",
            words=(TranscriptWord("hello", 0.0, 1.1, None),),
            provider_metadata={},
            elapsed_seconds=0.1,
        ).validate(language="en")
