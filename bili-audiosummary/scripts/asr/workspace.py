from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.asr.alignment import AlignmentContractError, TranscriptWord
from scripts.asr.pipeline_types import (
    ASR_PIPELINE_SCHEMA_VERSION,
    AsrPipelinePlan,
    ChunkTranscript,
    SourceIdentity,
)
from scripts.utils import read_json, write_json_atomic


def workspace_paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "plan": root / "asr_plan.json",
        "vad": root / "vad_result.json",
        "progress": root / "progress.json",
        "chunks": root / "chunk_results",
        "result": root / "result.json",
        "metrics": root / "metrics.json",
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
) -> AsrPipelinePlan | None:
    data = load_json_or_none(path)
    try:
        plan = AsrPipelinePlan.from_dict(data)
        expected_execution = execution_identity_factory(plan.source.sample_count)
    except (OSError, TypeError, ValueError):
        return None
    if (
        not plan.source.file_matches(audio_path)
        or plan.provider_request != provider_request
        or plan.execution_policy != expected_execution
    ):
        return None
    return plan


def write_vad_result(
    path: Path,
    plan: AsrPipelinePlan,
    speech_intervals: list[tuple[int, int]],
) -> None:
    write_json_atomic(
        path,
        {
            "schema_version": ASR_PIPELINE_SCHEMA_VERSION,
            "source": asdict(plan.source),
            "parameters": asdict(plan.vad_parameters),
            "speech_intervals": [
                {"start_sample": start, "end_sample": end}
                for start, end in speech_intervals
            ],
        },
    )


def load_valid_vad_result(
    path: Path, source: SourceIdentity, parameters: Any
) -> list[tuple[int, int]] | None:
    data = load_json_or_none(path)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != ASR_PIPELINE_SCHEMA_VERSION
        or data.get("source") != asdict(source)
        or data.get("parameters") != asdict(parameters)
        or not isinstance(data.get("speech_intervals"), list)
    ):
        return None
    intervals: list[tuple[int, int]] = []
    previous_end = 0
    for item in data["speech_intervals"]:
        if not isinstance(item, dict):
            return None
        start, end = item.get("start_sample"), item.get("end_sample")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < previous_end
            or end < start
            or end > source.sample_count
        ):
            return None
        intervals.append((start, end))
        previous_end = end
    return intervals


def chunk_payload(plan: AsrPipelinePlan, transcript: ChunkTranscript) -> dict[str, Any]:
    transcript.validate(language=str(plan.provider_request["language"]))
    return {
        "schema_version": ASR_PIPELINE_SCHEMA_VERSION,
        "plan": plan.to_dict(),
        **asdict(transcript),
    }


def transcript_from_payload(data: Any, plan: AsrPipelinePlan) -> ChunkTranscript | None:
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != ASR_PIPELINE_SCHEMA_VERSION
        or data.get("plan") != plan.to_dict()
    ):
        return None
    try:
        transcript = ChunkTranscript(
            chunk_index=data["chunk_index"],
            start_sample=data["start_sample"],
            end_sample=data["end_sample"],
            text=data["text"],
            words=tuple(TranscriptWord(**word) for word in data["words"]),
            provider_metadata=dict(data["provider_metadata"]),
            elapsed_seconds=float(data["elapsed_seconds"]),
        )
        layout = plan.chunks[transcript.chunk_index]
        if (
            transcript.chunk_index != layout.index
            or transcript.start_sample != layout.start_sample
            or transcript.end_sample != layout.end_sample
        ):
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


def rebuild_progress(
    plan: AsrPipelinePlan,
    results: dict[str, ChunkTranscript],
    failures: dict[str, str] | None = None,
) -> dict[str, Any]:
    failures = failures or {}
    return {
        "schema_version": ASR_PIPELINE_SCHEMA_VERSION,
        "plan": plan.to_dict(),
        "chunks": {
            (key := chunk_key(layout.index)): {
                "status": "succeeded"
                if key in results
                else ("failed" if key in failures else "pending"),
                "error": failures.get(key),
                "result_path": f"chunk_results/{key}.json",
            }
            for layout in plan.chunks
        },
    }
