from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from scripts.asr.chunking import (
    BOUNDARY_AUDIO_END as BOUNDARY_AUDIO_END,
)
from scripts.asr.chunking import (
    BOUNDARY_HARD,
    DEFAULT_VAD_PARAMETERS,
    MAX_CHUNK_SAMPLES,
    MIN_CHUNK_SAMPLES,
    SAMPLE_RATE,
    ChunkLayout,
    VadParameters,
    candidate_chunk_counts,
    plan_chunks,
    validate_layouts,
)
from scripts.asr.chunking import (
    BOUNDARY_SILENCE as BOUNDARY_SILENCE,
)
from scripts.asr.chunking import (
    PlanningParameters as SamplePlanningParameters,
)
from scripts.runtime_options import TranscribeOptions
from scripts.utils import path_to_posix

SCHEMA_VERSION = 6
MIN_ASR_CHUNK_SECONDS = MIN_CHUNK_SAMPLES / SAMPLE_RATE
MAX_ASR_CHUNK_SECONDS = MAX_CHUNK_SAMPLES / SAMPLE_RATE


@dataclass(frozen=True, init=False)
class AsrSourceAudio:
    path: str
    size: int
    mtime: float
    sample_count: int
    sample_rate: int

    def __init__(
        self,
        path: str,
        size: int,
        mtime: float,
        sample_count: int | None = None,
        sample_rate: int = SAMPLE_RATE,
        duration: float | None = None,
    ) -> None:
        if sample_count is None:
            if duration is None:
                raise TypeError("sample_count is required")
            sample_count = round(float(duration) * sample_rate)
        object.__setattr__(self, "path", str(path))
        object.__setattr__(self, "size", int(size))
        object.__setattr__(self, "mtime", float(mtime))
        object.__setattr__(self, "sample_count", int(sample_count))
        object.__setattr__(self, "sample_rate", int(sample_rate))

    @property
    def duration(self) -> float:
        return self.sample_count / self.sample_rate


@dataclass(frozen=True, init=False)
class PlanningParameters:
    min_chunk_samples: int
    max_chunk_samples: int

    def __init__(
        self,
        min_chunk_samples: int = MIN_CHUNK_SAMPLES,
        max_chunk_samples: int = MAX_CHUNK_SAMPLES,
        min_chunk_seconds: float | None = None,
        max_chunk_seconds: float | None = None,
    ) -> None:
        if min_chunk_seconds is not None:
            min_chunk_samples = round(float(min_chunk_seconds) * SAMPLE_RATE)
        if max_chunk_seconds is not None:
            max_chunk_samples = round(float(max_chunk_seconds) * SAMPLE_RATE)
        object.__setattr__(self, "min_chunk_samples", int(min_chunk_samples))
        object.__setattr__(self, "max_chunk_samples", int(max_chunk_samples))

    @property
    def min_chunk_seconds(self) -> float:
        return self.min_chunk_samples / SAMPLE_RATE

    @property
    def max_chunk_seconds(self) -> float:
        return self.max_chunk_samples / SAMPLE_RATE

    def as_sample_parameters(self) -> SamplePlanningParameters:
        return SamplePlanningParameters(self.min_chunk_samples, self.max_chunk_samples)


DEFAULT_PLANNING_PARAMETERS = PlanningParameters()


@dataclass(frozen=True)
class WorkerConfig:
    cpu_budget: int
    num_workers: int
    cpu_threads: int


@dataclass(frozen=True)
class AsrChunkPlan:
    index: int
    start_sample: int
    end_sample: int
    end_boundary: str
    estimated_speech_samples: int

    @property
    def start(self) -> float:
        return self.start_sample / SAMPLE_RATE

    @property
    def duration(self) -> float:
        return (self.end_sample - self.start_sample) / SAMPLE_RATE

    @property
    def estimated_speech_duration(self) -> float:
        return self.estimated_speech_samples / SAMPLE_RATE

    def as_layout(self) -> ChunkLayout:
        return ChunkLayout(
            self.index,
            self.start_sample,
            self.end_sample,
            self.end_boundary,
            self.estimated_speech_samples,
        )


@dataclass(frozen=True)
class ParallelAsrPlan:
    schema_version: int
    source_audio: AsrSourceAudio
    provider: str
    model: str | None
    language: str
    beam_size: int
    device: str
    compute_type: str
    vad_parameters: VadParameters
    planning_parameters: PlanningParameters
    count_strategy: str
    group_size: int
    cpu_budget: int
    num_workers: int
    cpu_threads: int
    chunks: list[AsrChunkPlan]

    @property
    def batch_count(self) -> int:
        return len(self.chunks) // self.num_workers

    @property
    def hard_cut_count(self) -> int:
        return sum(chunk.end_boundary == BOUNDARY_HARD for chunk in self.chunks)


def source_audio_fingerprint(
    audio_path: Path,
    sample_count: int | float,
    sample_rate: int = SAMPLE_RATE,
) -> AsrSourceAudio:
    stat = audio_path.stat()
    resolved_count = (
        round(sample_count * sample_rate)
        if isinstance(sample_count, float)
        else int(sample_count)
    )
    return AsrSourceAudio(
        path_to_posix(audio_path),
        stat.st_size,
        stat.st_mtime,
        resolved_count,
        sample_rate,
    )


def source_file_matches(source: AsrSourceAudio, audio_path: Path) -> bool:
    stat = audio_path.stat()
    return (
        source.path == path_to_posix(audio_path)
        and source.size == stat.st_size
        and source.mtime == stat.st_mtime
    )


def _options_value(options: Any, name: str, default: Any = None) -> Any:
    return getattr(options, name, default)


def _cpu_budget(cpu_count: int | None) -> int:
    return max(1, math.floor((cpu_count or 1) * 0.75))


def resolve_planning_parameters(options: TranscribeOptions | Any) -> PlanningParameters:
    requested = _options_value(options, "max_chunk_seconds", None)
    maximum = MAX_ASR_CHUNK_SECONDS if requested is None else float(requested)
    if not math.isfinite(maximum) or maximum < MIN_ASR_CHUNK_SECONDS:
        raise ValueError(
            f"Invalid max_chunk_seconds={requested!r}: expected at least 30."
        )
    return PlanningParameters(max_chunk_seconds=maximum)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Invalid {name}={value!r}: expected a positive integer.")
    return value


def resolve_worker_config(
    duration_or_samples: float | int,
    cpu_count: int | None,
    options: TranscribeOptions | Any,
    planning_parameters: PlanningParameters | None = None,
) -> WorkerConfig:
    parameters = planning_parameters or resolve_planning_parameters(options)
    sample_count = (
        round(duration_or_samples * SAMPLE_RATE)
        if isinstance(duration_or_samples, float)
        else int(duration_or_samples)
    )
    budget = _cpu_budget(cpu_count)
    requested_workers = _options_value(options, "num_workers", None)
    requested_threads = _options_value(options, "cpu_threads", None)
    if requested_workers is not None:
        requested_workers = _positive_int(requested_workers, "num_workers")
    if requested_threads is not None:
        requested_threads = _positive_int(requested_threads, "cpu_threads")

    def legal(workers: int) -> bool:
        return bool(
            candidate_chunk_counts(
                sample_count,
                group_size=workers,
                count_strategy="divisible",
                parameters=parameters.as_sample_parameters(),
            )
        )

    if requested_workers is not None:
        threads = (
            requested_threads
            if requested_threads is not None
            else budget // requested_workers
        )
        if threads < 1 or requested_workers * threads > budget:
            raise ValueError("Invalid worker configuration: exceeds CPU budget.")
        if not legal(requested_workers):
            raise ValueError(
                f"No legal chunk count for num_workers={requested_workers}."
            )
        return WorkerConfig(budget, requested_workers, threads)

    minimum_count = math.ceil(sample_count / parameters.max_chunk_samples)
    cap = min(budget, max(1, minimum_count))
    candidates = (
        workers
        for workers in range(cap, 0, -1)
        if requested_threads is not None or budget % workers == 0
    )
    for workers in candidates:
        threads = requested_threads or budget // workers
        if threads >= 1 and workers * threads <= budget and legal(workers):
            return WorkerConfig(budget, workers, threads)
    raise ValueError("No worker configuration can produce legal chunks.")


def _source_from_value(value: AsrSourceAudio | dict[str, Any]) -> AsrSourceAudio:
    if isinstance(value, AsrSourceAudio):
        return value
    return AsrSourceAudio(**value)


def _speech_to_samples(
    intervals: Iterable[Any], sample_count: int
) -> tuple[tuple[int, int], ...]:
    values = []
    for interval in intervals:
        if isinstance(interval, dict):
            start, end = interval["start"], interval["end"]
        else:
            start, end = interval
        if isinstance(start, float) or isinstance(end, float):
            start, end = (
                round(float(start) * SAMPLE_RATE),
                round(float(end) * SAMPLE_RATE),
            )
        values.append(
            (max(0, min(sample_count, int(start))), max(0, min(sample_count, int(end))))
        )
    return tuple(values)


def build_parallel_asr_plan(
    duration_seconds: float | None = None,
    cpu_count: int | None = None,
    source_audio: AsrSourceAudio | dict[str, Any] | None = None,
    options: TranscribeOptions | Any = None,
    speech_intervals: Iterable[Any] | None = None,
    vad_parameters: VadParameters = DEFAULT_VAD_PARAMETERS,
    worker_config: WorkerConfig | None = None,
    planning_parameters: PlanningParameters | None = None,
    *,
    sample_count: int | None = None,
) -> ParallelAsrPlan:
    if source_audio is None:
        raise TypeError("source_audio is required")
    source = _source_from_value(source_audio)
    count = source.sample_count if sample_count is None else int(sample_count)
    if duration_seconds is not None and round(duration_seconds * SAMPLE_RATE) != count:
        raise ValueError("Source duration does not match plan sample count.")
    parameters = planning_parameters or resolve_planning_parameters(options)
    config = worker_config or resolve_worker_config(
        count, cpu_count, options, parameters
    )
    speech = _speech_to_samples(speech_intervals or (), count)
    layouts = plan_chunks(
        count,
        speech,
        group_size=config.num_workers,
        count_strategy="divisible",
        parameters=parameters.as_sample_parameters(),
    )
    chunks = [
        AsrChunkPlan(
            item.index,
            item.start_sample,
            item.end_sample,
            item.end_boundary,
            item.estimated_speech_samples,
        )
        for item in layouts
    ]
    plan = ParallelAsrPlan(
        SCHEMA_VERSION,
        source,
        str(_options_value(options, "asr_provider", "whisper")),
        _options_value(options, "model", None),
        str(_options_value(options, "language", "zh")),
        int(_options_value(options, "beam_size", 5)),
        str(_options_value(options, "device", "cpu")),
        str(_options_value(options, "compute_type", "float32")),
        vad_parameters,
        parameters,
        "divisible",
        config.num_workers,
        config.cpu_budget,
        config.num_workers,
        config.cpu_threads,
        chunks,
    )
    _validate_plan(plan)
    return plan


def plan_matches_request(
    plan: ParallelAsrPlan,
    source_audio: AsrSourceAudio,
    options: TranscribeOptions | Any,
    worker_config: WorkerConfig,
    vad_parameters: VadParameters = DEFAULT_VAD_PARAMETERS,
    planning_parameters: PlanningParameters | None = None,
) -> bool:
    parameters = planning_parameters or resolve_planning_parameters(options)
    return (
        plan.schema_version == SCHEMA_VERSION
        and plan.source_audio == source_audio
        and plan.provider == str(_options_value(options, "asr_provider", "whisper"))
        and plan.model == _options_value(options, "model", None)
        and plan.language == str(_options_value(options, "language", "zh"))
        and plan.beam_size == int(_options_value(options, "beam_size", 5))
        and plan.device == str(_options_value(options, "device", "cpu"))
        and plan.compute_type == str(_options_value(options, "compute_type", "float32"))
        and plan.vad_parameters == vad_parameters
        and plan.planning_parameters == parameters
        and plan.count_strategy == "divisible"
        and plan.group_size == worker_config.num_workers
        and plan.cpu_budget == worker_config.cpu_budget
        and plan.num_workers == worker_config.num_workers
        and plan.cpu_threads == worker_config.cpu_threads
    )


def _validate_plan(plan: ParallelAsrPlan) -> None:
    if plan.schema_version != SCHEMA_VERSION:
        raise ValueError("Invalid ASR plan schema_version.")
    if plan.count_strategy != "divisible" or plan.group_size != plan.num_workers:
        raise ValueError("Invalid Whisper chunk count strategy.")
    if plan.num_workers * plan.cpu_threads > plan.cpu_budget:
        raise ValueError("ASR plan worker configuration exceeds its CPU budget.")
    if not plan.chunks or len(plan.chunks) % plan.num_workers:
        raise ValueError("ASR plan chunk count must be a positive worker multiple.")
    validate_layouts(
        (chunk.as_layout() for chunk in plan.chunks),
        plan.source_audio.sample_count,
        plan.planning_parameters.as_sample_parameters(),
    )


def plan_to_dict(plan: ParallelAsrPlan) -> dict[str, Any]:
    return asdict(plan)


def plan_from_dict(data: dict[str, Any]) -> ParallelAsrPlan:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Invalid ASR plan schema_version: expected {SCHEMA_VERSION}, got {data.get('schema_version') if isinstance(data, dict) else None}."
        )
    try:
        plan = ParallelAsrPlan(
            schema_version=int(data["schema_version"]),
            source_audio=AsrSourceAudio(**data["source_audio"]),
            provider=str(data["provider"]),
            model=data.get("model"),
            language=str(data["language"]),
            beam_size=int(data["beam_size"]),
            device=str(data["device"]),
            compute_type=str(data["compute_type"]),
            vad_parameters=VadParameters(**data["vad_parameters"]),
            planning_parameters=PlanningParameters(**data["planning_parameters"]),
            count_strategy=str(data["count_strategy"]),
            group_size=int(data["group_size"]),
            cpu_budget=int(data["cpu_budget"]),
            num_workers=int(data["num_workers"]),
            cpu_threads=int(data["cpu_threads"]),
            chunks=[AsrChunkPlan(**item) for item in data["chunks"]],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid ASR plan: {exc}") from exc
    _validate_plan(plan)
    return plan
