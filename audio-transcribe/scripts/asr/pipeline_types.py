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
from scripts.io_utils import canonical_sha256


def _require_exact_fields(data: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError(f"Invalid {name} fields.")
    return data


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"Invalid {name}.")
    return value


def _require_number(value: Any, name: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"Invalid {name}.")
    return value


def _validate_json_value(value: Any, name: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        _require_number(value, name)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, name)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json_value(item, name)
        return
    raise ValueError(f"Invalid {name}.")


def _same_json_shape(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _same_json_shape(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_shape(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


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
    source: SourceIdentity
    provider_request: dict[str, Any]
    execution_policy: dict[str, Any]
    vad_parameters: VadParameters
    planning_parameters: PlanningParameters
    chunks: tuple[ChunkLayout, ...]

    def validate(self) -> None:
        if (
            len(self.source.audio_id) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source.audio_id
            )
            or type(self.source.size) is not int
            or self.source.size < 0
            or type(self.source.sample_count) is not int
            or self.source.sample_count <= 0
            or self.source.sample_rate != SAMPLE_RATE
        ):
            raise ValueError("Invalid ASR source identity.")
        if not _same_json_shape(
            self.provider_request.get("alignment_policy"), ALIGNMENT_POLICY
        ):
            raise ValueError("Invalid ASR alignment policy.")
        _validate_json_value(self.provider_request, "ASR Provider request")
        _validate_json_value(self.execution_policy, "ASR execution policy")
        validate_layouts(
            self.chunks, self.source.sample_count, self.planning_parameters
        )

    def canonical_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["chunks"] = [asdict(chunk) for chunk in self.chunks]
        return data

    @property
    def plan_id(self) -> str:
        return canonical_sha256(self.canonical_payload())

    def to_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, **self.canonical_payload()}

    @classmethod
    def from_dict(cls, data: Any) -> AsrPipelinePlan:
        try:
            data = _require_exact_fields(
                data,
                {
                    "plan_id",
                    "source",
                    "provider_request",
                    "execution_policy",
                    "vad_parameters",
                    "planning_parameters",
                    "chunks",
                },
                "ASR plan",
            )
            source = _require_exact_fields(
                data["source"],
                {"audio_id", "size", "sample_count", "sample_rate"},
                "ASR source",
            )
            if (
                not isinstance(source["audio_id"], str)
                or len(source["audio_id"]) != 64
                or any(char not in "0123456789abcdef" for char in source["audio_id"])
            ):
                raise ValueError("Invalid source audio_id.")
            provider_request = data["provider_request"]
            execution_policy = data["execution_policy"]
            if not isinstance(provider_request, dict) or not isinstance(
                execution_policy, dict
            ):
                raise ValueError("Invalid ASR request identity.")
            vad = _require_exact_fields(
                data["vad_parameters"],
                {
                    "threshold",
                    "neg_threshold",
                    "min_speech_duration_ms",
                    "min_silence_duration_ms",
                    "max_speech_duration_s",
                    "speech_pad_ms",
                    "sampling_rate",
                },
                "VAD parameters",
            )
            planning = _require_exact_fields(
                data["planning_parameters"],
                {"min_chunk_samples", "max_chunk_samples"},
                "planning parameters",
            )
            chunks_data = data["chunks"]
            if not isinstance(chunks_data, list):
                raise ValueError("Invalid chunk layouts.")
            chunks: list[ChunkLayout] = []
            for item in chunks_data:
                item = _require_exact_fields(
                    item,
                    {
                        "index",
                        "start_sample",
                        "end_sample",
                        "end_boundary",
                        "estimated_speech_samples",
                    },
                    "chunk layout",
                )
                if not isinstance(item["end_boundary"], str):
                    raise ValueError("Invalid chunk boundary.")
                chunks.append(
                    ChunkLayout(
                        index=_require_int(item["index"], "chunk index"),
                        start_sample=_require_int(
                            item["start_sample"], "chunk start sample"
                        ),
                        end_sample=_require_int(item["end_sample"], "chunk end sample"),
                        end_boundary=item["end_boundary"],
                        estimated_speech_samples=_require_int(
                            item["estimated_speech_samples"],
                            "estimated speech samples",
                        ),
                    )
                )
            plan = cls(
                source=SourceIdentity(
                    audio_id=source["audio_id"],
                    size=_require_int(source["size"], "source size"),
                    sample_count=_require_int(
                        source["sample_count"], "source sample count", minimum=1
                    ),
                    sample_rate=_require_int(
                        source["sample_rate"], "source sample rate", minimum=1
                    ),
                ),
                provider_request=dict(provider_request),
                execution_policy=dict(execution_policy),
                vad_parameters=VadParameters(
                    threshold=_require_number(vad["threshold"], "VAD threshold"),
                    neg_threshold=_require_number(
                        vad["neg_threshold"], "VAD negative threshold"
                    ),
                    min_speech_duration_ms=_require_int(
                        vad["min_speech_duration_ms"], "minimum speech duration"
                    ),
                    min_silence_duration_ms=_require_int(
                        vad["min_silence_duration_ms"], "minimum silence duration"
                    ),
                    max_speech_duration_s=(
                        None
                        if vad["max_speech_duration_s"] is None
                        else _require_number(
                            vad["max_speech_duration_s"], "maximum speech duration"
                        )
                    ),
                    speech_pad_ms=_require_int(vad["speech_pad_ms"], "speech padding"),
                    sampling_rate=_require_int(
                        vad["sampling_rate"], "VAD sampling rate", minimum=1
                    ),
                ),
                planning_parameters=PlanningParameters(
                    min_chunk_samples=_require_int(
                        planning["min_chunk_samples"],
                        "minimum chunk samples",
                        minimum=1,
                    ),
                    max_chunk_samples=_require_int(
                        planning["max_chunk_samples"],
                        "maximum chunk samples",
                        minimum=1,
                    ),
                ),
                chunks=tuple(chunks),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid ASR pipeline plan: {exc}") from exc
        if plan.source.sample_rate != SAMPLE_RATE:
            raise ValueError("Invalid ASR source sample rate.")
        plan.validate()
        if type(data["plan_id"]) is not str or data["plan_id"] != plan.plan_id:
            raise ValueError("Invalid ASR plan identity.")
        return plan


@dataclass(frozen=True)
class PipelineMetrics:
    provider: str
    execution_policy: str
    total_elapsed_seconds: float
    provider_stage_seconds: float
    chunk_elapsed_seconds: tuple[dict[str, Any], ...]
    chunk_count: int
    batch_count: int
    hard_cut_count: int
    chunk_estimated_speech_durations: tuple[float, ...]
    max_estimated_speech_duration: float
    speech_load_msre: float
    failed_chunks: tuple[str, ...] = ()
    cpu_budget: int | None = None
    num_workers: int | None = None
    cpu_threads: int | None = None
    batch_size: int | None = None


@dataclass(frozen=True)
class PipelineOutcome:
    final_info: dict[str, Any]
    source: str
    metrics: PipelineMetrics


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
