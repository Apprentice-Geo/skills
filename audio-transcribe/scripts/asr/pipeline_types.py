from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scripts.asr.alignment import (
    ALIGNMENT_POLICY,
    AlignedTranscript,
    AlignmentContractError,
    CleanupReport,
    TranscriptWord,
    validate_alignment,
)
from scripts.asr.chunking import (
    SAMPLE_RATE,
    ChunkLayout,
    PlanningParameters,
    VadParameters,
    validate_layouts,
)

ASR_PIPELINE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SourceIdentity:
    audio_id: str
    size: int
    sample_count: int
    sample_rate: int = SAMPLE_RATE

    @property
    def duration(self) -> float:
        return self.sample_count / self.sample_rate

    @classmethod
    def from_path(cls, path: Path, sample_count: int) -> SourceIdentity:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return cls(digest.hexdigest(), stat.st_size, sample_count, SAMPLE_RATE)

    def file_matches(self, path: Path) -> bool:
        stat = path.stat()
        if self.size != stat.st_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return self.audio_id == digest.hexdigest()


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
        if self.provider_request.get("alignment_policy") != ALIGNMENT_POLICY:
            raise ValueError("Invalid ASR alignment policy.")
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
    cleanup_report: CleanupReport = CleanupReport()

    @property
    def alignment(self) -> AlignedTranscript:
        return AlignedTranscript(self.text, self.words)

    def validate_metadata(self) -> None:
        if type(self.chunk_index) is not int or self.chunk_index < 0:
            raise AlignmentContractError("Invalid chunk identity.")
        if (
            type(self.start_sample) is not int
            or type(self.end_sample) is not int
            or self.start_sample < 0
            or self.end_sample <= self.start_sample
        ):
            raise AlignmentContractError("Invalid chunk boundary.")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(self.elapsed_seconds)
            or self.elapsed_seconds < 0
        ):
            raise AlignmentContractError("Invalid chunk elapsed time.")
        if not isinstance(self.provider_metadata, dict):
            raise AlignmentContractError("Invalid Provider metadata.")

    def validate(self, *, language: str) -> None:
        self.validate_metadata()
        validate_alignment(
            self.alignment,
            (self.end_sample - self.start_sample) / SAMPLE_RATE,
            chunk_index=self.chunk_index,
            language=language,
        )
        report = self.cleanup_report
        if (
            isinstance(report.dropped_zero_duration_items, bool)
            or report.dropped_zero_duration_items < 0
            or (
                report.dropped_zero_duration_items == 0
                and (report.first_start is not None or report.last_end is not None)
            )
            or (
                report.dropped_zero_duration_items > 0
                and (
                    report.first_start is None
                    or report.last_end is None
                    or not math.isfinite(report.first_start)
                    or not math.isfinite(report.last_end)
                    or report.first_start < 0
                    or report.last_end < report.first_start
                    or report.last_end
                    > (self.end_sample - self.start_sample) / SAMPLE_RATE
                )
            )
        ):
            raise AlignmentContractError("Invalid alignment cleanup report.")
