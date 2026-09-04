from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

import pytest

from scripts.asr.alignment import ALIGNMENT_POLICY, TranscriptWord
from scripts.asr.chunking import ChunkLayout, PlanningParameters, VadParameters
from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript, SourceIdentity
from scripts.asr.workspace import (
    chunk_payload,
    load_chunk_results,
    load_valid_vad_result,
    transcript_from_payload,
    validate_vad_intervals,
    write_vad_result,
)
from scripts.io_utils import read_json
from scripts.utils import write_json


def _vad_payload(
    source: SourceIdentity,
    parameters: VadParameters,
    intervals: list[tuple[int, int]],
) -> dict:
    return {
        "source": asdict(source),
        "parameters": asdict(parameters),
        "speech_intervals": [
            {"start_sample": start, "end_sample": end} for start, end in intervals
        ],
    }


def test_vad_artifact_reports_stable_identity_and_file_reasons(
    workspace_tmp_path,
) -> None:
    path = workspace_tmp_path / "vad_result.json"
    source = SourceIdentity("a" * 64, 5, 4)
    parameters = VadParameters()

    assert load_valid_vad_result(path, source, parameters).reason == "missing"

    path.write_text("{", encoding="utf-8")
    assert load_valid_vad_result(path, source, parameters).reason == "unreadable"

    payload = _vad_payload(source, parameters, [(0, 4)])
    payload["schema_version"] = 2
    write_json(path, payload)
    assert load_valid_vad_result(path, source, parameters).reason == "invalid_structure"

    payload = _vad_payload(source, parameters, [(0, 4)])
    payload["source"]["sample_count"] = 5
    write_json(path, payload)
    assert load_valid_vad_result(path, source, parameters).reason == "source_mismatch"

    payload = _vad_payload(source, parameters, [(0, 4)])
    payload["parameters"]["threshold"] = 0.9
    write_json(path, payload)
    assert (
        load_valid_vad_result(path, source, parameters).reason == "parameters_mismatch"
    )


@pytest.mark.parametrize(
    "intervals",
    [
        [{"start_sample": True, "end_sample": 1}],
        [{"start_sample": -1, "end_sample": 1}],
        [{"start_sample": 1, "end_sample": 1}],
        [{"start_sample": 0, "end_sample": 5}],
        [
            {"start_sample": 0, "end_sample": 3},
            {"start_sample": 2, "end_sample": 4},
        ],
        [
            {"start_sample": 2, "end_sample": 4},
            {"start_sample": 0, "end_sample": 1},
        ],
    ],
)
def test_vad_artifact_rejects_invalid_interval_structure(
    workspace_tmp_path,
    intervals,
) -> None:
    path = workspace_tmp_path / "vad_result.json"
    source = SourceIdentity("a" * 64, 5, 4)
    parameters = VadParameters()
    payload = _vad_payload(source, parameters, [])
    payload["speech_intervals"] = intervals
    write_json(path, payload)

    assert load_valid_vad_result(path, source, parameters).reason == "invalid_structure"


def test_vad_artifact_accepts_sorted_adjacent_intervals(workspace_tmp_path) -> None:
    path = workspace_tmp_path / "vad_result.json"
    source = SourceIdentity("a" * 64, 5, 4)
    parameters = VadParameters()
    write_json(path, _vad_payload(source, parameters, [(0, 2), (2, 4)]))

    result = load_valid_vad_result(path, source, parameters)

    assert result.reason is None
    assert result.intervals == [(0, 2), (2, 4)]


def test_validate_vad_intervals_accepts_runtime_pairs_and_rejects_bool() -> None:
    source = SourceIdentity("a" * 64, 5, 4)

    assert validate_vad_intervals([(0, 2), (2, 4)], source) == [(0, 2), (2, 4)]
    with pytest.raises(ValueError, match="Invalid VAD speech interval"):
        validate_vad_intervals([(True, 2)], source)


def test_vad_writer_uses_current_strict_shape(workspace_tmp_path) -> None:
    path = workspace_tmp_path / "vad_result.json"
    source = SourceIdentity("a" * 64, 5, 4)
    parameters = VadParameters()

    write_vad_result(path, source, parameters, [(0, 4)])

    assert read_json(path) == _vad_payload(source, parameters, [(0, 4)])


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", True),
        ("sample_count", 4.0),
        ("sample_rate", "16000"),
    ],
)
def test_vad_artifact_rejects_source_type_coercion(
    workspace_tmp_path, field, value
) -> None:
    path = workspace_tmp_path / "vad_result.json"
    source = SourceIdentity("a" * 64, 5, 4)
    payload = _vad_payload(source, VadParameters(), [(0, 4)])
    payload["source"][field] = value
    write_json(path, payload)

    assert load_valid_vad_result(path, source, VadParameters()).reason == (
        "source_mismatch"
    )


def _plan() -> AsrPipelinePlan:
    return AsrPipelinePlan(
        source=SourceIdentity("a" * 64, 10, 16_000),
        provider_request={
            "provider": "fake",
            "language": "en",
            "alignment_policy": dict(ALIGNMENT_POLICY),
        },
        execution_policy={"policy": "serial"},
        vad_parameters=VadParameters(),
        planning_parameters=PlanningParameters(1, 16_000),
        chunks=(ChunkLayout(0, 0, 16_000, "audio_end", 8_000),),
    )


def _transcript() -> ChunkTranscript:
    return ChunkTranscript(
        chunk_index=0,
        start_sample=0,
        end_sample=16_000,
        text="hello",
        words=(TranscriptWord("hello", 0.0, 0.5, 0.9),),
        provider_metadata={"ignored": True},
        elapsed_seconds=1.25,
    )


def test_chunk_payload_contains_only_recoverable_content() -> None:
    plan = _plan()

    payload = chunk_payload(plan, _transcript())

    assert payload == {
        "plan_id": plan.plan_id,
        "chunk_index": 0,
        "text": "hello",
        "items": [{"text": "hello", "start": 0.0, "end": 0.5, "probability": 0.9}],
    }


def test_chunk_loader_restores_layout_from_plan() -> None:
    plan = _plan()

    transcript = transcript_from_payload(chunk_payload(plan, _transcript()), plan)

    assert transcript is not None
    assert (transcript.start_sample, transcript.end_sample) == (0, 16_000)
    assert transcript.provider_metadata == {}
    assert transcript.elapsed_seconds == 0.0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(schema_version=2),
        lambda data: data.update(plan_id="0" * 64),
        lambda data: data.update(chunk_index=True),
        lambda data: data["items"][0].update(start="0.0"),
        lambda data: data["items"][0].update(end=float("nan")),
        lambda data: data["items"][0].update(probability=True),
        lambda data: data["items"][0].update(extra="invalid"),
    ],
)
def test_chunk_loader_rejects_noncanonical_shape_and_values(mutation) -> None:
    plan = _plan()
    payload = deepcopy(chunk_payload(plan, _transcript()))
    mutation(payload)

    assert transcript_from_payload(payload, plan) is None


def test_chunk_loader_requires_filename_to_match_index(workspace_tmp_path) -> None:
    plan = _plan()
    chunks = workspace_tmp_path / "chunk_results"
    chunks.mkdir()
    write_json(chunks / "chunk_001.json", chunk_payload(plan, _transcript()))

    assert load_chunk_results(workspace_tmp_path, plan) == {}
