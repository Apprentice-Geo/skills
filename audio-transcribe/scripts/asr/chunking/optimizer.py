from __future__ import annotations

# The optimizer uses integer sample coordinates.
import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Iterable

from scripts.asr.chunking.timeline import Timeline


@dataclass(frozen=True)
class BoundaryOptimizationResult:
    boundaries: tuple[int, ...]
    speech_loads: tuple[int, ...]
    hard_cut_count: int
    max_speech_load: int


def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """
    合并重叠或相邻连续区间
    """
    ordered = sorted((lo, hi) for lo, hi in ranges if lo <= hi)
    merged: list[list[int]] = []
    for lo, hi in ordered:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _intersect_ranges(
    left: Iterable[tuple[int, int]],
    right: Iterable[tuple[int, int]],
) -> list[tuple[int, int]]:
    """
    对两个区间列表取交集
    """
    left_ranges = list(left)
    right_ranges = list(right)
    result: list[tuple[int, int]] = []
    left_index = 0
    right_index = 0
    while left_index < len(left_ranges) and right_index < len(right_ranges):
        left_lo, left_hi = left_ranges[left_index]
        right_lo, right_hi = right_ranges[right_index]
        lo = max(left_lo, right_lo)
        hi = min(left_hi, right_hi)
        if lo <= hi:
            result.append((lo, hi))
        if left_hi < right_hi:
            left_index += 1
        else:
            right_index += 1
    return result


def _clip_ranges(
    ranges: Iterable[tuple[int, int]],
    lo: int,
    hi: int,
) -> list[tuple[int, int]]:
    # 裁剪区间列表到指定范围
    if lo > hi:
        return []
    return _intersect_ranges(ranges, [(lo, hi)])


def _stage_window(
    duration: int,
    chunk_count: int,
    stage: int,
    minimum: int,
    maximum: int,
) -> tuple[int, int]:
    """
    基于 chunk 长度限制
    计算指定 chunk 的合法时间窗口范围
    """
    return (
        max(stage * minimum, duration - (chunk_count - stage) * maximum),
        min(stage * maximum, duration - (chunk_count - stage) * minimum),
    )


def _minimum_window_allowed_ranges(
    timeline: Timeline,
    minimum: int,
    maximum_load: int,
) -> list[tuple[int, int]]:
    if minimum > timeline.duration:
        return []
    events = {minimum, timeline.duration}
    for start, end in timeline.speech_intervals:
        for event in (start, end, start + minimum, end + minimum):
            if minimum <= event <= timeline.duration:
                events.add(event)
    ordered = sorted(events)
    allowed: list[tuple[int, int]] = []
    for lo, hi in pairwise(ordered):
        load = timeline.speech_at(lo) - timeline.speech_at(lo - minimum)
        slope = 0
        if lo < hi:
            slope = (
                timeline.speech_at(lo + 1)
                - timeline.speech_at(lo)
                - timeline.speech_at(lo + 1 - minimum)
                + timeline.speech_at(lo - minimum)
            )
        if slope == 0:
            if load <= maximum_load:
                allowed.append((lo, hi))
        elif slope > 0:
            allowed_hi = min(hi, lo + (maximum_load - load) // slope)
            if lo <= allowed_hi:
                allowed.append((lo, allowed_hi))
        else:
            allowed_lo = max(
                lo,
                lo + math.ceil((load - maximum_load) / -slope),
            )
            if allowed_lo <= hi:
                allowed.append((allowed_lo, hi))
    if ordered == [timeline.duration]:
        load = timeline.speech_at(timeline.duration) - timeline.speech_at(
            timeline.duration - minimum
        )
        if load <= maximum_load:
            allowed.append((timeline.duration, timeline.duration))
    return _merge_ranges(allowed)


def _advance_reachable(
    reachable: list[tuple[int, int]],
    timeline: Timeline,
    minimum: int,
    maximum: int,
    maximum_load: int | None,
    minimum_window_allowed: list[tuple[int, int]] | None,
) -> list[tuple[int, int]]:
    """
    根据上一边界的可达范围
    计算下一边界的可达范围
    """
    if maximum_load is None:
        return _merge_ranges(
            (lo + minimum, min(timeline.duration, hi + maximum)) for lo, hi in reachable
        )

    exact_minimum_ranges = [
        (lo + minimum, min(timeline.duration, hi + minimum))
        for lo, hi in reachable
        if lo + minimum <= timeline.duration
    ]
    exact_minimum = _intersect_ranges(
        _merge_ranges(exact_minimum_ranges),
        minimum_window_allowed or [],
    )
    fixed_predecessor: list[tuple[int, int]] = []
    for _lo, hi in reachable:
        target_lo = hi + minimum
        target_hi = min(
            timeline.duration,
            hi + maximum,
            timeline.latest_coordinate_with_speech_at_most(
                timeline.speech_at(hi) + maximum_load
            ),
        )
        if target_lo <= target_hi:
            fixed_predecessor.append((target_lo, target_hi))
    return _merge_ranges([*exact_minimum, *fixed_predecessor])


def _reachable_layers(
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
    maximum_load: int | None,
    hard_limit: int | None = None,
) -> list[dict[int, list[tuple[int, int]]]]:
    """
    在限制条件下寻找并返回可行切分
    """
    minimum_window_allowed = (
        None
        if maximum_load is None
        else _minimum_window_allowed_ranges(timeline, minimum, maximum_load)
    )
    """
    layers[i][j]
    恰使用 j 个硬切点
    第 i 个边界可达的时间范围列表
    """
    layers: list[dict[int, list[tuple[int, int]]]] = [{0: [(0, 0)]}]
    for stage in range(1, chunk_count + 1):
        window_lo, window_hi = _stage_window(
            timeline.duration,
            chunk_count,
            stage,
            minimum,
            maximum,
        )
        next_layer: dict[int, list[tuple[int, int]]] = {}
        for hard_used, ranges in layers[-1].items():
            """
            layers[i][j] 由 layers[i-1][j] 和 layers[i-1][j-1] 递推得到
            """
            advanced = _advance_reachable(
                ranges,
                timeline,
                minimum,
                maximum,
                maximum_load,
                minimum_window_allowed,
            )
            advanced = _clip_ranges(advanced, window_lo, window_hi)
            if not advanced:
                continue
            if stage == chunk_count:
                final = _intersect_ranges(
                    advanced, [(timeline.duration, timeline.duration)]
                )
                if final:
                    next_layer.setdefault(hard_used, []).extend(final)
                continue

            safe = _intersect_ranges(advanced, timeline.safe_ranges)
            if safe:
                next_layer.setdefault(hard_used, []).extend(safe)
            next_hard = hard_used + 1
            if hard_limit is None or next_hard <= hard_limit:
                hard = _intersect_ranges(advanced, timeline.hard_ranges)
                if hard:
                    next_layer.setdefault(next_hard, []).extend(hard)
        layers.append(
            {
                hard_used: _merge_ranges(ranges)
                for hard_used, ranges in next_layer.items()
                if hard_limit is None or hard_used <= hard_limit
            }
        )
    return layers


def _minimum_hard_cut_count(
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
) -> int:
    layers = _reachable_layers(
        timeline,
        chunk_count,
        minimum,
        maximum,
        maximum_load=None,
    )
    # 在可行切分中取硬切最少的
    feasible = [
        hard_used
        for hard_used, ranges in layers[-1].items()
        if (timeline.duration, timeline.duration) in ranges
    ]
    if not feasible:
        raise ValueError("Unable to construct a legal chunk boundary layout.")
    return min(feasible)


def _minimum_maximum_load(
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
    hard_cut_count: int,
) -> tuple[int, list[dict[int, list[tuple[int, int]]]]]:
    """
    二分答案
    确定最少硬切数时
    最小化最大语音切片长度
    """
    low = math.ceil(timeline.total_speech / chunk_count)
    high = timeline.total_speech
    best_layers: list[dict[int, list[tuple[int, int]]]] | None = None
    best_load: int | None = None
    while low < high:
        middle = (low + high) // 2
        layers = _reachable_layers(
            timeline,
            chunk_count,
            minimum,
            maximum,
            maximum_load=middle,
            hard_limit=hard_cut_count,
        )
        if hard_cut_count in layers[-1]:
            # middle 可行 减少负载
            high = middle
            best_layers = layers
            best_load = middle
        else:
            # middle 不可行 增加负载
            low = middle + 1
    if best_layers is None or best_load != low:
        best_layers = _reachable_layers(
            timeline,
            chunk_count,
            minimum,
            maximum,
            maximum_load=low,
            hard_limit=hard_cut_count,
        )
    if hard_cut_count not in best_layers[-1]:
        raise RuntimeError("Minimum speech-load feasibility search lost its solution.")
    return low, best_layers


def _backtrack_reachable_boundaries(
    layers: list[dict[int, list[tuple[int, int]]]],
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
    hard_cut_count: int,
    maximum_load: int | None,
) -> tuple[int, ...]:
    boundaries = [timeline.duration]
    coordinate = timeline.duration
    hard_used = hard_cut_count
    for stage in range(chunk_count, 0, -1):
        cut_cost = int(stage < chunk_count and timeline.is_hard(coordinate))
        previous_hard = hard_used - cut_cost
        if previous_hard < 0:
            raise RuntimeError("Invalid hard-cut predecessor while backtracking.")
        ranges = layers[stage - 1].get(previous_hard, [])
        lower = coordinate - maximum
        upper = coordinate - minimum
        if maximum_load is not None:
            lower = max(
                lower,
                timeline.earliest_coordinate_with_speech_at_least(
                    timeline.speech_at(coordinate) - maximum_load
                ),
            )
        predecessor: int | None = None
        for range_lo, range_hi in reversed(ranges):
            candidate_lo = max(range_lo, lower)
            candidate_hi = min(range_hi, upper)
            if candidate_lo <= candidate_hi:
                predecessor = candidate_hi
                break
        if predecessor is None:
            raise RuntimeError("ASR optimizer predecessor is missing.")
        boundaries.append(predecessor)
        coordinate = predecessor
        hard_used = previous_hard
    boundaries.reverse()
    return tuple(boundaries)


def _balanced_boundaries(duration: int, chunk_count: int) -> tuple[int, ...]:
    short, remainder = divmod(duration, chunk_count)
    durations = [short] * (chunk_count - remainder) + [short + 1] * remainder
    boundaries = [0]
    for chunk_duration in durations:
        boundaries.append(boundaries[-1] + chunk_duration)
    return tuple(boundaries)


def _earliest_legal_boundaries(
    duration: int, chunk_count: int, minimum: int, maximum: int
) -> tuple[int, ...]:
    boundaries = [0]
    for stage in range(1, chunk_count):
        boundaries.append(
            max(boundaries[-1] + minimum, duration - (chunk_count - stage) * maximum)
        )
    boundaries.append(duration)
    return tuple(boundaries)


def _result_from_boundaries(
    timeline: Timeline,
    boundaries: tuple[int, ...],
) -> BoundaryOptimizationResult:
    loads = tuple(
        timeline.speech_at(end) - timeline.speech_at(start)
        for start, end in pairwise(boundaries)
    )
    hard_cut_count = sum(timeline.is_hard(value) for value in boundaries[1:-1])
    return BoundaryOptimizationResult(
        boundaries=boundaries,
        speech_loads=loads,
        hard_cut_count=hard_cut_count,
        max_speech_load=max(loads, default=0),
    )


def optimize_chunk_boundaries(
    *,
    duration_samples: int,
    chunk_count: int,
    speech_intervals: Iterable[tuple[int, int]],
    min_chunk_samples: int = 60_000,
    max_chunk_samples: int = 300_000,
) -> BoundaryOptimizationResult:
    if (
        isinstance(duration_samples, bool)
        or isinstance(chunk_count, bool)
        or isinstance(min_chunk_samples, bool)
        or isinstance(max_chunk_samples, bool)
        or not all(
            isinstance(value, int)
            for value in (
                duration_samples,
                chunk_count,
                min_chunk_samples,
                max_chunk_samples,
            )
        )
    ):
        raise ValueError("Boundary optimizer inputs must use integer samples.")
    if duration_samples <= 0 or chunk_count <= 0:
        raise ValueError("Duration and chunk count must be positive.")
    if min_chunk_samples <= 0 or max_chunk_samples < min_chunk_samples:
        raise ValueError("Invalid chunk duration bounds.")
    if not (
        chunk_count * min_chunk_samples
        <= duration_samples
        <= chunk_count * max_chunk_samples
    ):
        raise ValueError("Chunk count cannot satisfy the requested duration bounds.")

    timeline = Timeline.build(duration_samples, speech_intervals)
    if chunk_count == 1:
        return _result_from_boundaries(timeline, (0, duration_samples))
    if timeline.total_speech == 0:
        boundaries = _earliest_legal_boundaries(
            duration_samples,
            chunk_count,
            min_chunk_samples,
            max_chunk_samples,
        )
        return _result_from_boundaries(timeline, boundaries)

    if timeline.total_speech == duration_samples:
        boundaries = _balanced_boundaries(duration_samples, chunk_count)
        return _result_from_boundaries(timeline, boundaries)

    hard_cut_count = _minimum_hard_cut_count(
        timeline,
        chunk_count,
        min_chunk_samples,
        max_chunk_samples,
    )
    maximum_load, load_layers = _minimum_maximum_load(
        timeline,
        chunk_count,
        min_chunk_samples,
        max_chunk_samples,
        hard_cut_count,
    )
    feasible_boundaries = _backtrack_reachable_boundaries(
        load_layers,
        timeline,
        chunk_count,
        min_chunk_samples,
        max_chunk_samples,
        hard_cut_count,
        maximum_load,
    )
    return _result_from_boundaries(timeline, feasible_boundaries)
