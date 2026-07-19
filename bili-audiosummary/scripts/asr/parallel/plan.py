from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from scripts.asr.parallel.optimizer import optimize_chunk_boundaries
from scripts.runtime_options import TranscribeOptions
from scripts.utils import path_to_posix


SCHEMA_VERSION = 5
MIN_ASR_CHUNK_SECONDS = 30.0
MAX_ASR_CHUNK_SECONDS = 180.0
# VAD 参数
VAD_THRESHOLD = 0.35
VAD_NEG_THRESHOLD = 0.25
VAD_MIN_SPEECH_DURATION_MS = 0
VAD_MIN_SILENCE_DURATION_MS = 300
VAD_MAX_SPEECH_DURATION_S: float | None = None
VAD_SPEECH_PAD_MS = 0
VAD_SAMPLING_RATE = 16_000
BOUNDARY_SILENCE = "silence"
BOUNDARY_HARD = "hard"
BOUNDARY_AUDIO_END = "audio_end"
BOUNDARY_TYPES = {BOUNDARY_SILENCE, BOUNDARY_HARD, BOUNDARY_AUDIO_END}
_EPSILON = 1e-9


@dataclass(frozen=True)
class AsrSourceAudio:
    path: str
    size: int
    mtime: float
    duration: float


@dataclass(frozen=True)
class VadParameters:
    threshold: float = VAD_THRESHOLD
    neg_threshold: float = VAD_NEG_THRESHOLD
    min_speech_duration_ms: int = VAD_MIN_SPEECH_DURATION_MS
    min_silence_duration_ms: int = VAD_MIN_SILENCE_DURATION_MS
    max_speech_duration_s: float | None = VAD_MAX_SPEECH_DURATION_S
    speech_pad_ms: int = VAD_SPEECH_PAD_MS
    sampling_rate: int = VAD_SAMPLING_RATE


@dataclass(frozen=True)
class PlanningParameters:
    min_chunk_seconds: float = MIN_ASR_CHUNK_SECONDS
    max_chunk_seconds: float = MAX_ASR_CHUNK_SECONDS


DEFAULT_VAD_PARAMETERS = VadParameters()
DEFAULT_PLANNING_PARAMETERS = PlanningParameters()


@dataclass(frozen=True)
class WorkerConfig:
    cpu_budget: int
    num_workers: int
    cpu_threads: int


@dataclass(frozen=True)
class AsrChunkPlan:
    index: int
    start: float
    duration: float
    path: str
    end_boundary: str
    estimated_speech_duration: float


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


def _round_seconds(value: float) -> float:
    return round(float(value), 3)


def source_audio_fingerprint(audio_path: Path, duration_seconds: float) -> AsrSourceAudio:
    stat = audio_path.stat()
    return AsrSourceAudio(
        path=path_to_posix(audio_path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        duration=_round_seconds(duration_seconds),
    )


def _options_value(options: Any, name: str, default: Any = None) -> Any:
    return getattr(options, name, default)


def _cpu_budget(cpu_count: int | None) -> int:
    return max(1, math.floor((cpu_count or 1) * 0.75))


def resolve_planning_parameters(options: TranscribeOptions | Any) -> PlanningParameters:
    requested_maximum = _options_value(options, "max_chunk_seconds", None)
    maximum = (
        MAX_ASR_CHUNK_SECONDS
        if requested_maximum is None
        else float(requested_maximum)
    )
    parameters = PlanningParameters(max_chunk_seconds=_round_seconds(maximum))
    if (
        not math.isfinite(parameters.max_chunk_seconds)
        or parameters.max_chunk_seconds < parameters.min_chunk_seconds
    ):
        raise ValueError(
            f"Invalid max_chunk_seconds={requested_maximum!r}: expected at least "
            f"{parameters.min_chunk_seconds:.0f}."
        )
    return parameters


def _chunk_count_range(
    duration_seconds: float, planning_parameters: PlanningParameters
) -> tuple[int, int]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError(f"Invalid audio duration: {duration_seconds!r}.")
    if duration_seconds < planning_parameters.min_chunk_seconds:
        return 1, 1
    minimum = math.ceil(
        duration_seconds / planning_parameters.max_chunk_seconds - _EPSILON
    )
    maximum = math.floor(
        duration_seconds / planning_parameters.min_chunk_seconds + _EPSILON
    )
    if minimum > maximum:
        raise ValueError("Unable to split audio within the configured chunk bounds.")
    return minimum, maximum


def _candidate_chunk_counts(
    duration_seconds: float,
    num_workers: int,
    planning_parameters: PlanningParameters,
) -> list[int]:
    """升序返回候选音频切片数。"""
    minimum, maximum = _chunk_count_range(duration_seconds, planning_parameters)
    return [count for count in range(minimum, maximum + 1) if count % num_workers == 0]


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Invalid {name}={value!r}: expected a positive integer.")
    return value


def resolve_worker_config(
    duration_seconds: float,
    cpu_count: int | None,
    options: TranscribeOptions | Any,
    planning_parameters: PlanningParameters | None = None,
) -> WorkerConfig:
    """确认 worker 和 thread 的配置。"""
    duration = _round_seconds(duration_seconds)
    parameters = planning_parameters or resolve_planning_parameters(options)
    cpu_budget = _cpu_budget(cpu_count)
    requested_workers = _options_value(options, "num_workers", None)
    requested_threads = _options_value(options, "cpu_threads", None)
    if requested_workers is not None:
        requested_workers = _positive_int(requested_workers, "num_workers")
    if requested_threads is not None:
        requested_threads = _positive_int(requested_threads, "cpu_threads")

    if requested_workers is not None:
        # 每个 worker 的线程数相同
        cpu_threads = (
            requested_threads
            if requested_threads is not None
            else cpu_budget // requested_workers
        )
        if cpu_threads < 1 or requested_workers * cpu_threads > cpu_budget:
            raise ValueError("Invalid worker configuration: exceeds CPU budget.")
        if not _candidate_chunk_counts(duration, requested_workers, parameters):
            raise ValueError(
                f"No legal chunk count for num_workers={requested_workers} and "
                f"duration={duration:.3f}s."
            )
        return WorkerConfig(cpu_budget, requested_workers, cpu_threads)

    required_chunks = _chunk_count_range(duration, parameters)[0]
    worker_cap = min(cpu_budget, required_chunks)
    if requested_threads is not None:
        if requested_threads > cpu_budget:
            raise ValueError("Invalid cpu_threads: exceeds CPU budget.")
        worker_cap = min(worker_cap, cpu_budget // requested_threads)
        worker_candidates = range(worker_cap, 0, -1)
    else:
        # 取可整除预算的 worker 数候选
        worker_candidates = (
            workers
            for workers in range(worker_cap, 0, -1)
            if cpu_budget % workers == 0
        )
    for num_workers in worker_candidates:
        # 优先取最多且可保证 chunk 负载均衡的 worker 数量
        if _candidate_chunk_counts(duration, num_workers, parameters):
            return WorkerConfig(
                cpu_budget,
                num_workers,
                requested_threads or cpu_budget // num_workers,
            )
    raise ValueError("No worker configuration can produce legal chunks.")


def _interval_values(interval: Any) -> tuple[float, float]:
    # 兼容字典和元组结构
    if isinstance(interval, dict):
        return float(interval["start"]), float(interval["end"])
    start, end = interval
    return float(start), float(end)


def _speech_intervals_ms(
    speech_intervals: Iterable[Any], duration_ms: int
) -> tuple[tuple[int, int], ...]:
    # 将 VAD 结果转换为毫秒整数区间，并裁剪到音频范围内
    values: list[tuple[int, int]] = []
    duration = duration_ms / 1000
    for interval in speech_intervals:
        start, end = _interval_values(interval)
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError(f"Invalid speech interval: {(start, end)!r}.")
        start = max(0.0, min(duration, start))
        end = max(0.0, min(duration, end))
        start_ms = round(start * 1000)
        end_ms = round(end * 1000)
        if end_ms > start_ms:
            values.append((start_ms, end_ms))
    return tuple(values)


def _chunk_path(index: int) -> str:
    return path_to_posix(Path("chunks") / f"chunk_{index:03d}.wav")


def _source_from_value(source_audio: AsrSourceAudio | dict[str, Any]) -> AsrSourceAudio:
    if isinstance(source_audio, AsrSourceAudio):
        return AsrSourceAudio(
            source_audio.path,
            source_audio.size,
            source_audio.mtime,
            _round_seconds(source_audio.duration),
        )
    return AsrSourceAudio(
        path=str(source_audio["path"]),
        size=int(source_audio["size"]),
        mtime=float(source_audio["mtime"]),
        duration=_round_seconds(source_audio["duration"]),
    )


def build_parallel_asr_plan(
    duration_seconds: float,
    cpu_count: int | None,
    source_audio: AsrSourceAudio | dict[str, Any],
    options: TranscribeOptions | Any,
    speech_intervals: Iterable[Any] | None = None,
    vad_parameters: VadParameters = DEFAULT_VAD_PARAMETERS,
    worker_config: WorkerConfig | None = None,
    planning_parameters: PlanningParameters | None = None,
) -> ParallelAsrPlan:
    duration = _round_seconds(duration_seconds)
    duration_ms = round(duration * 1000)
    source = _source_from_value(source_audio)
    if source.duration != duration:
        raise ValueError("Source duration does not match plan duration.")
    parameters = planning_parameters or resolve_planning_parameters(options)
    config = worker_config or resolve_worker_config(
        duration, cpu_count, options, parameters
    )
    intervals_ms = _speech_intervals_ms(speech_intervals or [], duration_ms)
    candidates = []
    for chunk_count in _candidate_chunk_counts(duration, config.num_workers, parameters):
        result = optimize_chunk_boundaries(
            duration_ms=duration_ms,
            chunk_count=chunk_count,
            speech_intervals_ms=intervals_ms,
            min_chunk_ms=min(
                duration_ms, round(parameters.min_chunk_seconds * 1000)
            ),
            max_chunk_ms=round(parameters.max_chunk_seconds * 1000),
        )
        '''
        1. 优先减少语音内部硬切；
        2. 硬切数相同时减少批次数；
        3. 再降低最重 chunk 的预计语音量；
        4. 再均衡整体语音负载；
        5. 最后用切点序列保证结果确定性。
        '''
        rank = (
            result.hard_cut_count,
            chunk_count // config.num_workers,
            result.max_speech_load_ms,
            round(result.speech_load_msre, 15),
            result.boundaries_ms,
        )
        candidates.append((rank, result))
        if result.hard_cut_count == 0:
            break
    if not candidates:
        raise ValueError("Unable to build a valid ASR chunk plan.")
    result = min(candidates, key=lambda item: item[0])[1]
    chunks = []
    for index, (start_ms, end_ms, speech_ms) in enumerate(
        zip(result.boundaries_ms, result.boundaries_ms[1:], result.speech_loads_ms)
    ):
        boundary = BOUNDARY_AUDIO_END
        if index < len(result.speech_loads_ms) - 1:
            boundary = (
                BOUNDARY_HARD
                if any(start < end_ms < end for start, end in intervals_ms)
                else BOUNDARY_SILENCE
            )
        chunks.append(
            AsrChunkPlan(
                index=index,
                start=_round_seconds(start_ms / 1000),
                duration=_round_seconds((end_ms - start_ms) / 1000),
                path=_chunk_path(index),
                end_boundary=boundary,
                estimated_speech_duration=_round_seconds(speech_ms / 1000),
            )
        )
    plan = ParallelAsrPlan(
        schema_version=SCHEMA_VERSION,
        source_audio=source,
        provider=str(_options_value(options, "asr_provider", "whisper")),
        model=_options_value(options, "model", None),
        language=str(_options_value(options, "language", "zh")),
        beam_size=int(_options_value(options, "beam_size", 5)),
        device=str(_options_value(options, "device", "cpu")),
        compute_type=str(_options_value(options, "compute_type", "float32")),
        vad_parameters=vad_parameters,
        planning_parameters=parameters,
        cpu_budget=config.cpu_budget,
        num_workers=config.num_workers,
        cpu_threads=config.cpu_threads,
        chunks=chunks,
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
        and plan.cpu_budget == worker_config.cpu_budget
        and plan.num_workers == worker_config.num_workers
        and plan.cpu_threads == worker_config.cpu_threads
    )


def _validate_plan(plan: ParallelAsrPlan) -> None:
    if plan.schema_version != SCHEMA_VERSION:
        raise ValueError("Invalid ASR plan schema_version.")
    if plan.num_workers * plan.cpu_threads > plan.cpu_budget:
        raise ValueError("ASR plan worker configuration exceeds its CPU budget.")
    if not plan.chunks or len(plan.chunks) % plan.num_workers:
        raise ValueError("ASR plan chunk count must be a positive worker multiple.")
    duration = plan.source_audio.duration
    short_audio = duration < plan.planning_parameters.min_chunk_seconds
    if short_audio and (len(plan.chunks) != 1 or plan.num_workers != 1):
        raise ValueError("Audio shorter than 30 seconds must use one worker and chunk.")
    previous_end = 0.0
    for index, chunk in enumerate(plan.chunks):
        if chunk.index != index or chunk.path != _chunk_path(index):
            raise ValueError("Invalid ASR chunk identity.")
        if chunk.start != _round_seconds(previous_end):
            raise ValueError("ASR chunks must continuously cover the source audio.")
        if chunk.duration <= 0 or chunk.duration > plan.planning_parameters.max_chunk_seconds:
            raise ValueError("Invalid ASR chunk duration.")
        if not short_audio and chunk.duration < plan.planning_parameters.min_chunk_seconds:
            raise ValueError("Invalid ASR chunk duration.")
        if chunk.estimated_speech_duration < 0 or chunk.estimated_speech_duration > chunk.duration:
            raise ValueError("Invalid estimated speech duration.")
        if chunk.end_boundary not in BOUNDARY_TYPES:
            raise ValueError("Invalid ASR chunk boundary.")
        if index == len(plan.chunks) - 1:
            if chunk.end_boundary != BOUNDARY_AUDIO_END:
                raise ValueError("Final chunk must end at audio_end.")
        elif chunk.end_boundary == BOUNDARY_AUDIO_END:
            raise ValueError("Only final chunk may use audio_end.")
        previous_end = chunk.start + chunk.duration
    if _round_seconds(previous_end) != _round_seconds(duration):
        raise ValueError("ASR chunks do not cover the complete source audio.")


def plan_to_dict(plan: ParallelAsrPlan) -> dict[str, Any]:
    return asdict(plan)


def plan_from_dict(data: dict[str, Any]) -> ParallelAsrPlan:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Invalid ASR plan schema_version: expected {SCHEMA_VERSION}, "
            f"got {data.get('schema_version') if isinstance(data, dict) else None}."
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
            cpu_budget=int(data["cpu_budget"]),
            num_workers=int(data["num_workers"]),
            cpu_threads=int(data["cpu_threads"]),
            chunks=[AsrChunkPlan(**chunk) for chunk in data["chunks"]],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid ASR plan: {exc}") from exc
    _validate_plan(plan)
    return plan
