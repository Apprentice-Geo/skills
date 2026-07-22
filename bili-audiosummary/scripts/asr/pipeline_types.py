from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scripts.asr.alignment import (
    AlignmentContractError,
    TranscriptWord,
    validate_alignment_contract,
)
from scripts.asr.chunking import (
    SAMPLE_RATE,
    ChunkLayout,
    PlanningParameters,
    VadParameters,
    validate_layouts,
)
from scripts.utils import path_to_posix

ASR_PIPELINE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SourceIdentity:
    path: str
    size: int
    mtime: float
    sample_count: int
    sample_rate: int = SAMPLE_RATE

    @property
    def duration(self) -> float:
        return self.sample_count / self.sample_rate

    @classmethod
    def from_path(cls, path: Path, sample_count: int) -> SourceIdentity:
        stat = path.stat()
        return cls(
            path_to_posix(path), stat.st_size, stat.st_mtime, sample_count, SAMPLE_RATE
        )

    def file_matches(self, path: Path) -> bool:
        stat = path.stat()
        return (
            self.path == path_to_posix(path)
            and self.size == stat.st_size
            and self.mtime == stat.st_mtime
        )


@dataclass(frozen=True)
class AsrPipelinePlan:
    schema_version: int
    source: SourceIdentity
    provider_request: dict[str, Any]
    execution_policy: dict[str, Any]
    vad_parameters: VadParameters
    planning_parameters: PlanningParameters
    chunks: tuple[ChunkLayout, ...]

    def validate(self) -> None:
        if self.schema_version != ASR_PIPELINE_SCHEMA_VERSION:
            raise ValueError("Invalid ASR pipeline schema_version.")
        validate_layouts(
            self.chunks, self.source.sample_count, self.planning_parameters
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chunks"] = [asdict(chunk) for chunk in self.chunks]
        return data

    @classmethod
    def from_dict(cls, data: Any) -> AsrPipelinePlan:
        if not isinstance(data, dict):
            raise ValueError("ASR plan must be an object.")
        if data.get("schema_version") != ASR_PIPELINE_SCHEMA_VERSION:
            raise ValueError("Invalid ASR pipeline schema_version.")
        try:
            plan = cls(
                schema_version=int(data["schema_version"]),
                source=SourceIdentity(**data["source"]),
                provider_request=dict(data["provider_request"]),
                execution_policy=dict(data["execution_policy"]),
                vad_parameters=VadParameters(**data["vad_parameters"]),
                planning_parameters=PlanningParameters(**data["planning_parameters"]),
                chunks=tuple(ChunkLayout(**item) for item in data["chunks"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid ASR pipeline plan: {exc}") from exc
        plan.validate()
        return plan


@dataclass(frozen=True)
class ChunkTranscript:
    chunk_index: int
    start_sample: int
    end_sample: int
    text: str
    words: tuple[TranscriptWord, ...]
    provider_metadata: dict[str, Any]
    elapsed_seconds: float

    def validate(self, *, language: str) -> None:
        if self.chunk_index < 0 or self.start_sample < 0:
            raise AlignmentContractError("Invalid chunk identity.")
        if self.end_sample <= self.start_sample:
            raise AlignmentContractError("Invalid chunk boundary.")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise AlignmentContractError("Invalid chunk elapsed time.")
        validate_alignment_contract(
            self.text,
            list(self.words),
            (self.end_sample - self.start_sample) / SAMPLE_RATE,
            chunk_index=self.chunk_index,
            language=language,
        )
