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
from scripts.asr.merge import merge_chunk_transcripts
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


def test_legacy_timestamp_policy_invalidates_private_plan() -> None:
    plan = _plan()
    payload = plan.canonical_payload()
    payload["provider_request"]["alignment_policy"]["timestamp_resolution_ms"] = 1
    legacy_plan_id = canonical_sha256(payload)
    assert legacy_plan_id != plan.plan_id
    with pytest.raises(ValueError, match="alignment policy"):
        AsrPipelinePlan.from_dict({"plan_id": legacy_plan_id, **payload})


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


@pytest.mark.parametrize("start_sample,end_sample", [(1, 102378), (1, 9)])
def test_merge_preserves_sample_boundaries_and_adjacent_chunk_order(
    start_sample: int, end_sample: int
) -> None:
    sample_rate = 16000
    plan = AsrPipelinePlan(
        source=SourceIdentity("a" * 64, 10, end_sample + 16, sample_rate),
        provider_request={
            "provider": "fake",
            "language": "zh",
            "alignment_policy": dict(ALIGNMENT_POLICY),
        },
        execution_policy={"policy": "serial", "num_workers": 1},
        vad_parameters=VadParameters(),
        planning_parameters=PlanningParameters(1, end_sample + 16),
        chunks=(
            ChunkLayout(0, start_sample, end_sample, "audio_end", 1),
            ChunkLayout(1, end_sample, end_sample + 16, "audio_end", 1),
        ),
    )
    results = {
        f"chunk_{layout.index:03d}": ChunkTranscript(
            layout.index,
            layout.start_sample,
            layout.end_sample,
            "词",
            (TranscriptWord("词", 0.0, layout.sample_count / sample_rate),),
            {},
            0.0,
        )
        for layout in plan.chunks
    }
    merged = merge_chunk_transcripts(plan, results)
    assert merged.items[0].start == start_sample / sample_rate
    assert merged.items[0].end == end_sample / sample_rate
    assert merged.items[1].start == merged.items[0].end
    assert merged.items[1].end == plan.source.duration
