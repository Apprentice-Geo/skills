from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from scripts.runtime_options import TranscribeOptions
from scripts.utils import path_to_posix


SCHEMA_VERSION = 4
MIN_ASR_CHUNK_SECONDS = 60.0
MAX_ASR_CHUNK_SECONDS = 300.0
VAD_THRESHOLD = 0.5
VAD_MIN_SPEECH_DURATION_MS = 250
VAD_MIN_SILENCE_DURATION_MS = 500
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
    min_speech_duration_ms: int = VAD_MIN_SPEECH_DURATION_MS
    min_silence_duration_ms: int = VAD_MIN_SILENCE_DURATION_MS
    speech_pad_ms: int = VAD_SPEECH_PAD_MS
    sampling_rate: int = VAD_SAMPLING_RATE


DEFAULT_VAD_PARAMETERS = VadParameters()


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
    cpu_budget: int
    num_workers: int
    cpu_threads: int
    chunks: list[AsrChunkPlan]


@dataclass(frozen=True)
class _BoundaryPlan:
    hard_cut_count: int
    squared_error: float
    boundaries: tuple[float, ...]
    end_boundaries: tuple[str, ...]


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


def _round_seconds(value: float) -> float:
    return round(float(value), 3)


def _cpu_budget(cpu_count: int | None) -> int:
    return max(1, math.floor((cpu_count or 1) * 0.75))


def _chunk_count_range(duration_seconds: float) -> tuple[int, int]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError(f"Invalid audio duration: {duration_seconds!r}.")
    if duration_seconds < MIN_ASR_CHUNK_SECONDS:
        return 1, 1
    minimum = math.ceil(duration_seconds / MAX_ASR_CHUNK_SECONDS - _EPSILON)
    maximum = math.floor(duration_seconds / MIN_ASR_CHUNK_SECONDS + _EPSILON)
    if minimum > maximum:
        raise ValueError(
            f"Unable to split {duration_seconds:.3f}s audio into "
            f"{MIN_ASR_CHUNK_SECONDS:.0f}s-{MAX_ASR_CHUNK_SECONDS:.0f}s chunks."
        )
    return minimum, maximum


def _candidate_chunk_counts(duration_seconds: float, num_workers: int) -> list[int]:
    '''
    升序返回候选音频切片数
    '''
    minimum, maximum = _chunk_count_range(duration_seconds)
    return [
        count
        for count in range(minimum, maximum + 1)
        if count % num_workers == 0
    ]


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Invalid {name}={value!r}: expected a positive integer.")
    return value


def resolve_worker_config(
    duration_seconds: float,
    cpu_count: int | None,
    options: TranscribeOptions | Any,
) -> WorkerConfig:
    '''
    确认 worker 和 thread 的配置
    '''
    duration = _round_seconds(duration_seconds)
    cpu_budget = _cpu_budget(cpu_count)
    requested_workers = _options_value(options, "num_workers", None)
    requested_threads = _options_value(options, "cpu_threads", None)

    if requested_workers is not None:
        requested_workers = _positive_int(requested_workers, "num_workers")
    if requested_threads is not None:
        requested_threads = _positive_int(requested_threads, "cpu_threads")

    if requested_workers is not None:
        num_workers = requested_workers
        # 每个 worker 的线程数相同
        cpu_threads = (
            requested_threads
            if requested_threads is not None
            else cpu_budget // num_workers
        )
        if cpu_threads < 1 or num_workers * cpu_threads > cpu_budget:
            raise ValueError(
                "Invalid worker configuration: "
                f"num_workers={num_workers} * cpu_threads={cpu_threads} "
                f"exceeds cpu_budget={cpu_budget}."
            )
        if not _candidate_chunk_counts(duration, num_workers):
            raise ValueError(
                f"No legal chunk count for num_workers={num_workers} and "
                f"duration={duration:.3f}s."
            )
        return WorkerConfig(cpu_budget, num_workers, cpu_threads)

    if requested_threads is not None:
        if requested_threads > cpu_budget:
            raise ValueError(
                f"Invalid cpu_threads={requested_threads}: exceeds cpu_budget={cpu_budget}."
            )
        worker_limit = cpu_budget // requested_threads
        worker_candidates = range(worker_limit, 0, -1)
    else:
        # 取可整除预算的 worker 数候选
        worker_candidates = (
            workers
            for workers in range(cpu_budget, 0, -1)
            if cpu_budget % workers == 0
        )

    for num_workers in worker_candidates:
        # 优先取最多且可保证 chunk 负载均衡的 worker 数量
        if _candidate_chunk_counts(duration, num_workers):
            cpu_threads = (
                requested_threads
                if requested_threads is not None
                else cpu_budget // num_workers
            )
            return WorkerConfig(cpu_budget, num_workers, cpu_threads)
    raise ValueError(
        f"No worker configuration can produce legal chunks for duration={duration:.3f}s."
    )


def _interval_values(interval: Any) -> tuple[float, float]:
    # 兼容字典和元组结构
    if isinstance(interval, dict):
        return float(interval["start"]), float(interval["end"])
    start, end = interval
    return float(start), float(end)


def natural_cut_points(
    speech_intervals: Iterable[Any],
    duration_seconds: float,
) -> list[float]:
    '''
    根据 VAD 的语音区间结果 计算静音切分点
    '''
    duration = _round_seconds(duration_seconds)
    normalized: list[tuple[float, float]] = []
    for interval in speech_intervals:
        start, end = _interval_values(interval)
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError(f"Invalid speech interval: {(start, end)!r}.")
        start = max(0.0, min(duration, start))
        end = max(0.0, min(duration, end))
        if end > start:
            normalized.append((start, end))
    normalized.sort()

    merged: list[list[float]] = []
    # 合并重叠的语音区间
    for start, end in normalized:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    # 取前后两个相邻语音区间的中点作为切分点
    cut_points = {
        _round_seconds((left[1] + right[0]) / 2)
        for left, right in zip(merged, merged[1:])
        if right[0] > left[1]
    }
    return sorted(point for point in cut_points if 0.0 < point < duration)


def _transition_ranges(
    anchors: list[float],
    chunk_count: int,
) -> list[list[tuple[int, int]]]:
    ranges: list[list[tuple[int, int]]] = [[] for _ in range(chunk_count + 1)]
    for chunks_on_edge in range(1, chunk_count + 1):
        minimum = chunks_on_edge * MIN_ASR_CHUNK_SECONDS - _EPSILON
        maximum = chunks_on_edge * MAX_ASR_CHUNK_SECONDS + _EPSILON
        ranges[chunks_on_edge] = [
            (
                bisect_left(anchors, start + minimum, index + 1),
                bisect_right(anchors, start + maximum, index + 1),
            )
            for index, start in enumerate(anchors)
        ]
    return ranges


def _plan_with_hard_cut_count(
    duration: float,
    chunk_count: int,
    anchors: list[float],
    target: float,
    hard_cut_count: int,
    transition_ranges: list[list[tuple[int, int]]],
) -> _BoundaryPlan | None:
    '''
    计算给定硬切点数的音频切片计划
    '''
    # 边是两个静音切点之间的
    edge_count = chunk_count - hard_cut_count
    anchor_count = len(anchors)
    final_index = anchor_count - 1
    costs = [[math.inf] * anchor_count for _ in range(hard_cut_count + 1)]
    # costs[hard_used][anchor_index]
    # 用 hard_used 个硬切点，到达 anchors[anchor_index] 时的最小累计误差
    costs[0][0] = 0.0
    predecessor_layers: list[
        list[list[tuple[int, int, int] | None]]
    ] = []

    # 接下来依次枚举每条边的切点选择
    for edge_index in range(edge_count):
        remaining_edges = edge_count - edge_index - 1
        next_costs = [
            [math.inf] * anchor_count for _ in range(hard_cut_count + 1)
        ]
        predecessors = [
            [None] * anchor_count for _ in range(hard_cut_count + 1)
        ]
        found = False
        for hard_used, costs_by_anchor in enumerate(costs):
            for previous_index, previous_cost in enumerate(costs_by_anchor):
                if not math.isfinite(previous_cost):
                    continue
                # 枚举当前边使用的硬切点数
                for edge_hard_cuts in range(hard_cut_count - hard_used + 1):
                    next_hard_used = hard_used + edge_hard_cuts
                    chunks_on_edge = edge_hard_cuts + 1
                    # 下一个 anchor 的合法范围 左闭右开
                    first_destination, last_destination = transition_ranges[
                        chunks_on_edge
                    ][previous_index]
                    remaining_chunks = (
                        remaining_edges + hard_cut_count - next_hard_used
                    )
                    # 处理最后一条边
                    if remaining_edges == 0:
                        if next_hard_used != hard_cut_count:
                            continue
                        first_destination = max(first_destination, final_index)
                        last_destination = min(last_destination, final_index + 1)
                    else:
                        # chunk 长度约束
                        earliest = (
                            duration
                            - remaining_chunks * MAX_ASR_CHUNK_SECONDS
                            - _EPSILON
                        )
                        latest = (
                            duration
                            - remaining_chunks * MIN_ASR_CHUNK_SECONDS
                            + _EPSILON
                        )
                        # chunk 数量约束
                        first_destination = max(
                            first_destination,
                            bisect_left(anchors, earliest),
                        )
                        last_destination = min(
                            last_destination,
                            bisect_right(anchors, latest),
                            final_index - remaining_edges + 1,
                        )
                    start = anchors[previous_index]
                    for next_index in range(first_destination, last_destination):
                        distance = anchors[next_index] - start
                        # 计算当前边的误差 可能包含多个 chunk
                        edge_error = chunks_on_edge * (
                            distance / chunks_on_edge - target
                        ) ** 2
                        candidate_cost = previous_cost + edge_error
                        if round(candidate_cost, 12) >= round(
                            next_costs[next_hard_used][next_index],
                            12,
                        ):
                            continue
                        next_costs[next_hard_used][next_index] = candidate_cost
                        # 记录切点选择 用于回溯出方案
                        predecessors[next_hard_used][next_index] = (
                            previous_index,
                            hard_used,
                            edge_hard_cuts,
                        )
                        found = True
        if not found:
            return None
        # costs 用了滚动数组
        costs = next_costs
        predecessor_layers.append(predecessors)

    squared_error = costs[hard_cut_count][final_index]
    if not math.isfinite(squared_error):
        return None

    edges: list[tuple[int, int, int]] = []
    next_index = final_index
    hard_used = hard_cut_count
    for edge_index in range(edge_count - 1, -1, -1):
        predecessor = predecessor_layers[edge_index][hard_used][next_index]
        if predecessor is None:
            raise RuntimeError("ASR boundary plan predecessor is missing.")
        previous_index, previous_hard_used, edge_hard_cuts = predecessor
        edges.append((previous_index, next_index, edge_hard_cuts))
        next_index = previous_index
        hard_used = previous_hard_used
    edges.reverse()

    boundaries = [0.0]
    end_boundaries: list[str] = []
    for edge_number, (start_index, end_index, edge_hard_cuts) in enumerate(edges):
        start = anchors[start_index]
        end = anchors[end_index]
        chunks_on_edge = edge_hard_cuts + 1
        boundaries.extend(
            _round_seconds(start + (end - start) * offset / chunks_on_edge)
            for offset in range(1, chunks_on_edge)
        )
        end_boundaries.extend([BOUNDARY_HARD] * edge_hard_cuts)
        boundaries.append(_round_seconds(end))
        end_boundaries.append(
            BOUNDARY_AUDIO_END
            if edge_number == len(edges) - 1
            else BOUNDARY_SILENCE
        )
    return _BoundaryPlan(
        hard_cut_count=hard_cut_count,
        squared_error=squared_error,
        boundaries=tuple(boundaries),
        end_boundaries=tuple(end_boundaries),
    )


def _fixed_chunk_count_plan(
    duration_seconds: float,
    chunk_count: int,
    natural_boundaries: list[float],
) -> _BoundaryPlan:
    duration = _round_seconds(duration_seconds)
    if duration < MIN_ASR_CHUNK_SECONDS and chunk_count == 1:
        return _BoundaryPlan(
            hard_cut_count=0,
            squared_error=0.0,
            boundaries=(0.0, duration),
            end_boundaries=(BOUNDARY_AUDIO_END,),
        )
    target = duration / chunk_count
    anchors = [0.0, *natural_boundaries, duration]
    transition_ranges = _transition_ranges(anchors, chunk_count)
    for hard_cut_count in range(chunk_count):
        plan = _plan_with_hard_cut_count(
            duration,
            chunk_count,
            anchors,
            target,
            hard_cut_count,
            transition_ranges,
        )
        if plan is not None:
            return plan
    raise ValueError(
        f"Unable to build a {chunk_count}-chunk plan for {duration:.3f}s audio."
    )


def _chunk_path(index: int) -> str:
    return path_to_posix(Path("chunks") / f"chunk_{index:03d}.wav")


def _source_from_value(source_audio: AsrSourceAudio | dict[str, Any]) -> AsrSourceAudio:
    if isinstance(source_audio, AsrSourceAudio):
        return AsrSourceAudio(
            path=source_audio.path,
            size=source_audio.size,
            mtime=source_audio.mtime,
            duration=_round_seconds(source_audio.duration),
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
) -> ParallelAsrPlan:
    duration = _round_seconds(duration_seconds)
    source = _source_from_value(source_audio)
    if source.duration != duration:
        raise ValueError(
            f"Source duration {source.duration:.3f}s does not match plan duration {duration:.3f}s."
        )
    config = worker_config or resolve_worker_config(duration, cpu_count, options)
    natural_boundaries = natural_cut_points(speech_intervals or [], duration)
    candidates: list[tuple[tuple[Any, ...], _BoundaryPlan]] = []
    for chunk_count in _candidate_chunk_counts(duration, config.num_workers):
        boundary_plan = _fixed_chunk_count_plan(
            duration,
            chunk_count,
            natural_boundaries,
        )
        candidates.append(
            (
                (
                    boundary_plan.hard_cut_count,
                    chunk_count // config.num_workers,
                    round(boundary_plan.squared_error, 12),
                    boundary_plan.boundaries,
                ),
                boundary_plan,
            )
        )
        if boundary_plan.hard_cut_count == 0:
            break
    if not candidates:
        raise ValueError("Unable to build a valid ASR chunk plan.")
    _, selected = min(candidates, key=lambda item: item[0])

    chunks = [
        AsrChunkPlan(
            index=index,
            start=selected.boundaries[index],
            duration=_round_seconds(
                selected.boundaries[index + 1] - selected.boundaries[index]
            ),
            path=_chunk_path(index),
            end_boundary=selected.end_boundaries[index],
        )
        for index in range(len(selected.end_boundaries))
    ]
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
) -> bool:
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
        and plan.cpu_budget == worker_config.cpu_budget
        and plan.num_workers == worker_config.num_workers
        and plan.cpu_threads == worker_config.cpu_threads
    )


def _validate_plan(plan: ParallelAsrPlan) -> None:
    if plan.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"Invalid ASR plan schema_version: expected {SCHEMA_VERSION}, "
            f"got {plan.schema_version}."
        )
    if plan.cpu_budget < 1 or plan.num_workers < 1 or plan.cpu_threads < 1:
        raise ValueError("Invalid ASR plan worker configuration.")
    if plan.num_workers * plan.cpu_threads > plan.cpu_budget:
        raise ValueError("ASR plan worker configuration exceeds its CPU budget.")
    if not plan.chunks or len(plan.chunks) % plan.num_workers != 0:
        raise ValueError("ASR plan chunk count must be a positive worker multiple.")

    duration = plan.source_audio.duration
    previous_end = 0.0
    short_audio = duration < MIN_ASR_CHUNK_SECONDS
    if short_audio and len(plan.chunks) != 1:
        raise ValueError("Audio shorter than 60 seconds must use one chunk.")
    for index, chunk in enumerate(plan.chunks):
        if chunk.index != index or chunk.path != _chunk_path(index):
            raise ValueError(f"Invalid ASR chunk identity at index {index}.")
        if chunk.start != _round_seconds(previous_end):
            raise ValueError("ASR chunks must continuously cover the source audio.")
        if chunk.duration <= 0 or chunk.duration > MAX_ASR_CHUNK_SECONDS:
            raise ValueError(f"Invalid ASR chunk duration: {chunk.duration}.")
        if not short_audio and chunk.duration < MIN_ASR_CHUNK_SECONDS:
            raise ValueError(f"Invalid ASR chunk duration: {chunk.duration}.")
        if chunk.end_boundary not in BOUNDARY_TYPES:
            raise ValueError(f"Invalid ASR chunk end boundary: {chunk.end_boundary}.")
        if index == len(plan.chunks) - 1:
            if chunk.end_boundary != BOUNDARY_AUDIO_END:
                raise ValueError("The final ASR chunk must end at the audio boundary.")
        elif chunk.end_boundary == BOUNDARY_AUDIO_END:
            raise ValueError("Only the final ASR chunk may use audio_end.")
        previous_end = chunk.start + chunk.duration
    if _round_seconds(previous_end) != _round_seconds(duration):
        raise ValueError("ASR chunks do not cover the complete source audio.")


def plan_to_dict(plan: ParallelAsrPlan) -> dict[str, Any]:
    return asdict(plan)


def plan_from_dict(data: dict[str, Any]) -> ParallelAsrPlan:
    if not isinstance(data, dict):
        raise ValueError("Invalid ASR plan: root must be an object.")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Invalid ASR plan schema_version: expected {SCHEMA_VERSION}, "
            f"got {data.get('schema_version')}."
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
            cpu_budget=int(data["cpu_budget"]),
            num_workers=int(data["num_workers"]),
            cpu_threads=int(data["cpu_threads"]),
            chunks=[AsrChunkPlan(**chunk) for chunk in data["chunks"]],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid ASR plan: {exc}") from exc
    _validate_plan(plan)
    return plan
