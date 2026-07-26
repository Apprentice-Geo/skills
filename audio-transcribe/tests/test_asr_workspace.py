from __future__ import annotations

from dataclasses import asdict

import pytest

from scripts.asr.chunking import VadParameters
from scripts.asr.pipeline_types import ASR_PIPELINE_SCHEMA_VERSION, SourceIdentity
from scripts.asr.workspace import load_valid_vad_result
from scripts.utils import write_json


def _vad_payload(
    source: SourceIdentity,
    parameters: VadParameters,
    intervals: list[tuple[int, int]],
) -> dict:
    return {
        "schema_version": ASR_PIPELINE_SCHEMA_VERSION,
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
    payload["schema_version"] = 99
    write_json(path, payload)
    assert load_valid_vad_result(path, source, parameters).reason == "schema_mismatch"

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
