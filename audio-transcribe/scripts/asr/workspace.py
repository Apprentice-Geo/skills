from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from scripts.asr.alignment import AlignmentContractError, TranscriptWord
from scripts.asr.chunking import VadParameters
from scripts.asr.pipeline_types import (
    AsrPipelinePlan,
    ChunkTranscript,
    SourceIdentity,
)
from scripts.utils import read_json, write_json_atomic

VadArtifactReason = Literal[
    "missing",
    "unreadable",
    "source_mismatch",
    "parameters_mismatch",
    "invalid_structure",
]


@dataclass(frozen=True)
class VadArtifactValidation:
    intervals: list[tuple[int, int]] | None
    reason: VadArtifactReason | None


def workspace_paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "plan": root / "asr_plan.json",
        "vad": root / "vad_result.json",
        "chunks": root / "chunk_results",
        "result": root / "result.json",
    }


def chunk_key(index: int) -> str:
    return f"chunk_{index:03d}"


def load_json_or_none(path: Path) -> Any | None:
    try:
        return read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def load_matching_plan(
    path: Path,
    audio_path: Path,
    provider_request: dict[str, Any],
    execution_identity_factory: Any,
    vad_parameters: Any,
    planning_parameters: Any,
) -> AsrPipelinePlan | None:
    data = load_json_or_none(path)
    try:
        plan = AsrPipelinePlan.from_dict(data)
        expected_execution = execution_identity_factory(plan.source.sample_count)
    except (OSError, TypeError, ValueError):
        return None
    if (
        not plan.source.file_matches(audio_path)
        or not _same_json_value(plan.provider_request, provider_request)
        or not _same_json_value(plan.execution_policy, expected_execution)
        or plan.vad_parameters != vad_parameters
        or plan.planning_parameters != planning_parameters
    ):
        return None
    return plan


def write_vad_result(
    path: Path,
    source: SourceIdentity,
    parameters: VadParameters,
    speech_intervals: list[tuple[int, int]],
) -> None:
    write_json_atomic(
        path,
        {
            "source": asdict(source),
            "parameters": asdict(parameters),
            "speech_intervals": [
                {"start_sample": start, "end_sample": end}
                for start, end in speech_intervals
            ],
        },
    )


def load_valid_vad_result(
    path: Path, source: SourceIdentity, parameters: VadParameters
) -> VadArtifactValidation:
    if not path.exists():
        return VadArtifactValidation(None, "missing")
    try:
        data = read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return VadArtifactValidation(None, "unreadable")
    if not isinstance(data, dict):
        return VadArtifactValidation(None, "invalid_structure")
    if set(data) != {"source", "parameters", "speech_intervals"}:
        return VadArtifactValidation(None, "invalid_structure")
    if not _matches_dataclass_payload(data["source"], asdict(source)):
        return VadArtifactValidation(None, "source_mismatch")
    if not _matches_dataclass_payload(data["parameters"], asdict(parameters)):
        return VadArtifactValidation(None, "parameters_mismatch")
    if not isinstance(data["speech_intervals"], list):
        return VadArtifactValidation(None, "invalid_structure")
    try:
        intervals = validate_vad_intervals(data["speech_intervals"], source)
    except ValueError:
        return VadArtifactValidation(None, "invalid_structure")
    return VadArtifactValidation(intervals, None)


def validate_vad_intervals(
    intervals: Any, source: SourceIdentity
) -> list[tuple[int, int]]:
    if not isinstance(intervals, list):
        raise ValueError("VAD speech intervals must be a list.")
    validated: list[tuple[int, int]] = []
    previous_end = 0
    for item in intervals:
        if isinstance(item, dict):
            if set(item) != {"start_sample", "end_sample"}:
                raise ValueError("Invalid VAD speech interval fields.")
            start, end = item["start_sample"], item["end_sample"]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            start, end = item
        else:
            raise ValueError("Invalid VAD speech interval.")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or start < previous_end
            or end <= start
            or end > source.sample_count
        ):
            raise ValueError("Invalid VAD speech interval.")
        validated.append((start, end))
        previous_end = end
    return validated


def chunk_payload(plan: AsrPipelinePlan, transcript: ChunkTranscript) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "chunk_index": transcript.chunk_index,
        "text": transcript.text,
        "items": [asdict(item) for item in transcript.words],
    }


def transcript_from_payload(data: Any, plan: AsrPipelinePlan) -> ChunkTranscript | None:
    if (
        not isinstance(data, dict)
        or set(data) != {"plan_id", "chunk_index", "text", "items"}
        or data["plan_id"] != plan.plan_id
        or type(data["chunk_index"]) is not int
        or not isinstance(data["text"], str)
        or not isinstance(data["items"], list)
    ):
        return None
    try:
        chunk_index = data["chunk_index"]
        layout = plan.chunks[chunk_index]
        words = []
        for item in data["items"]:
            if not isinstance(item, dict) or set(item) != {
                "text",
                "start",
                "end",
                "probability",
            }:
                return None
            if not isinstance(item["text"], str):
                return None
            for field in ("start", "end"):
                value = item[field]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    return None
            probability = item["probability"]
            if probability is not None and (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not math.isfinite(probability)
            ):
                return None
            words.append(
                TranscriptWord(
                    text=item["text"],
                    start=item["start"],
                    end=item["end"],
                    probability=probability,
                )
            )
        transcript = ChunkTranscript(
            chunk_index=chunk_index,
            start_sample=layout.start_sample,
            end_sample=layout.end_sample,
            text=data["text"],
            words=tuple(words),
            provider_metadata={},
            elapsed_seconds=0.0,
        )
        if transcript.chunk_index != layout.index:
            return None
        transcript.validate(language=str(plan.provider_request["language"]))
    except (AlignmentContractError, IndexError, KeyError, TypeError, ValueError):
        return None
    return transcript


def load_chunk_results(root: Path, plan: AsrPipelinePlan) -> dict[str, ChunkTranscript]:
    results: dict[str, ChunkTranscript] = {}
    directory = workspace_paths(root)["chunks"]
    if not directory.exists():
        return results
    for path in directory.glob("chunk_*.json"):
        transcript = transcript_from_payload(load_json_or_none(path), plan)
        if transcript is not None and path.stem == chunk_key(transcript.chunk_index):
            results[path.stem] = transcript
    return results


def _matches_dataclass_payload(data: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(data, dict) or set(data) != set(expected):
        return False
    return all(
        type(data[key]) is type(expected_value) and data[key] == expected_value
        for key, expected_value in expected.items()
    )


def _same_json_value(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right
