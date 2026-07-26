from __future__ import annotations

# The optimizer uses integer sample coordinates.
import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Iterable

from scripts.asr.chunking.timeline import Timeline

_EXHAUSTIVE_HARD_POINT_LIMIT = 10_000


@dataclass(frozen=True)
class BoundaryOptimizationResult:
    boundaries: tuple[int, ...]
    speech_loads: tuple[int, ...]
    hard_cut_count: int
    max_speech_load: int
    speech_load_msre: float


@dataclass(frozen=True)
class _BoundaryNode:
    key: tuple[object, ...]
    lo: int
    hi: int
    speech: int
    hard: int


@dataclass(frozen=True)
class _PathLabel:
    node: _BoundaryNode
    reachable_lo: int
    reachable_hi: int
    hard_used: int
    square_sum: int
    path: tuple[_BoundaryNode, ...]
    path_key: tuple[tuple[int, int, int], ...]


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
) -> tuple[int, list[dict[int, list[tuple[int, int]]]]]:
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
    return min(feasible), layers


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


def _hard_feasible_ranges_by_stage(
    forward_layers: list[dict[int, list[tuple[int, int]]]],
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
    hard_cut_count: int,
    maximum_load: int,
) -> list[list[tuple[int, int]]]:
    reverse_timeline = Timeline.build(
        timeline.duration,
        (
            (timeline.duration - end, timeline.duration - start)
            for start, end in reversed(timeline.speech_intervals)
        ),
    )
    reverse_layers = _reachable_layers(
        reverse_timeline,
        chunk_count,
        minimum,
        maximum,
        maximum_load=maximum_load,
        hard_limit=hard_cut_count,
    )
    feasible: list[list[tuple[int, int]]] = [[] for _ in range(chunk_count + 1)]
    for stage in range(1, chunk_count):
        reverse_stage = chunk_count - stage
        stage_ranges: list[tuple[int, int]] = []
        for forward_hard, forward_ranges in forward_layers[stage].items():
            for reverse_hard, reverse_ranges in reverse_layers[reverse_stage].items():
                if forward_hard + reverse_hard - 1 != hard_cut_count:
                    continue
                mapped_reverse = [
                    (timeline.duration - hi, timeline.duration - lo)
                    for lo, hi in reversed(reverse_ranges)
                ]
                stage_ranges.extend(
                    _intersect_ranges(
                        _intersect_ranges(forward_ranges, mapped_reverse),
                        timeline.hard_ranges,
                    )
                )
        feasible[stage] = _merge_ranges(stage_ranges)
    return feasible


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
    total = timeline.total_speech
    chunk_count = len(loads)
    if total:
        msre = sum((chunk_count * load - total) ** 2 for load in loads) / (
            chunk_count * total * total
        )
    else:
        msre = 0.0
    return BoundaryOptimizationResult(
        boundaries=boundaries,
        speech_loads=loads,
        hard_cut_count=hard_cut_count,
        max_speech_load=max(loads, default=0),
        speech_load_msre=msre,
    )


def _safe_nodes_by_stage(
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
) -> list[list[_BoundaryNode]]:
    nodes: list[list[_BoundaryNode]] = [[_BoundaryNode(("start",), 0, 0, 0, 0)]]
    for stage in range(1, chunk_count):
        window_lo, window_hi = _stage_window(
            timeline.duration,
            chunk_count,
            stage,
            minimum,
            maximum,
        )
        stage_nodes: list[_BoundaryNode] = []
        for index, (safe_lo, safe_hi) in enumerate(timeline.safe_ranges):
            lo = max(window_lo, safe_lo)
            hi = min(window_hi, safe_hi)
            if lo <= hi:
                stage_nodes.append(
                    _BoundaryNode(
                        ("safe", stage, index),
                        lo,
                        hi,
                        timeline.speech_at(lo),
                        0,
                    )
                )
        nodes.append(stage_nodes)
    nodes.append(
        [
            _BoundaryNode(
                ("end",),
                timeline.duration,
                timeline.duration,
                timeline.total_speech,
                0,
            )
        ]
    )
    return nodes


def _prune_path_labels(labels: list[_PathLabel]) -> list[_PathLabel]:
    deduplicated: dict[tuple[int, int, int], _PathLabel] = {}
    for label in labels:
        key = (label.reachable_lo, label.reachable_hi, label.square_sum)
        current = deduplicated.get(key)
        if current is None or label.path_key < current.path_key:
            deduplicated[key] = label

    kept: list[_PathLabel] = []
    for candidate in sorted(
        deduplicated.values(),
        key=lambda item: (
            item.square_sum,
            item.reachable_lo,
            -item.reachable_hi,
            item.path_key,
        ),
    ):
        candidate_path = candidate.path_key
        dominated = False
        for current in kept:
            if (
                current.square_sum <= candidate.square_sum
                and current.reachable_lo <= candidate.reachable_lo
                and current.reachable_hi >= candidate.reachable_hi
                and (
                    current.square_sum < candidate.square_sum
                    or current.path_key <= candidate_path
                )
            ):
                dominated = True
                break
        if dominated:
            continue
        kept = [
            current
            for current in kept
            if not (
                candidate.square_sum <= current.square_sum
                and candidate.reachable_lo <= current.reachable_lo
                and candidate.reachable_hi >= current.reachable_hi
                and (
                    candidate.square_sum < current.square_sum
                    or candidate_path < current.path_key
                )
            )
        ]
        kept.append(candidate)
    return kept


def _reconstruct_earliest_boundaries(
    path: tuple[_BoundaryNode, ...],
    duration: int,
    minimum: int,
    maximum: int,
) -> tuple[int, ...] | None:
    allowed: list[tuple[int, int]] = [(node.lo, node.hi) for node in path]
    next_lo = duration
    next_hi = duration
    backward: list[tuple[int, int]] = []
    for node_lo, node_hi in reversed(allowed):
        lo = max(node_lo, next_lo - maximum)
        hi = min(node_hi, next_hi - minimum)
        if lo > hi:
            return None
        backward.append((lo, hi))
        next_lo = lo
        next_hi = hi
    backward.reverse()

    boundaries = [0]
    previous = 0
    for lo, hi in backward:
        value = max(lo, previous + minimum)
        if value > hi or value > previous + maximum:
            return None
        boundaries.append(value)
        previous = value
    if not minimum <= duration - previous <= maximum:
        return None
    boundaries.append(duration)
    return tuple(boundaries)


def _add_hard_candidate(
    candidates: list[set[int]],
    timeline: Timeline,
    stage: int,
    value: int | None,
    chunk_count: int,
    minimum: int,
    maximum: int,
) -> None:
    if value is None or not 0 < stage < chunk_count:
        return
    window_lo, window_hi = _stage_window(
        timeline.duration,
        chunk_count,
        stage,
        minimum,
        maximum,
    )
    if window_lo <= value <= window_hi and timeline.is_hard(value):
        candidates[stage].add(value)


def _hard_candidates_by_stage(
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
    hard_cut_count: int,
    maximum_load: int,
    safe_nodes: list[list[_BoundaryNode]],
    feasible_boundaries: tuple[int, ...],
    feasible_hard_ranges: list[list[tuple[int, int]]],
) -> list[set[int]]:
    candidates = [set() for _ in range(chunk_count + 1)]
    for stage, boundary in enumerate(feasible_boundaries[1:-1], start=1):
        _add_hard_candidate(
            candidates,
            timeline,
            stage,
            boundary,
            chunk_count,
            minimum,
            maximum,
        )

    hard_span = 0
    for stage in range(1, chunk_count):
        window_lo, window_hi = _stage_window(
            timeline.duration,
            chunk_count,
            stage,
            minimum,
            maximum,
        )
        for hard_lo, hard_hi in feasible_hard_ranges[stage]:
            lo = max(window_lo, hard_lo)
            hi = min(window_hi, hard_hi)
            if lo <= hi:
                hard_span += hi - lo + 1
                _add_hard_candidate(
                    candidates,
                    timeline,
                    stage,
                    lo,
                    chunk_count,
                    minimum,
                    maximum,
                )
                _add_hard_candidate(
                    candidates,
                    timeline,
                    stage,
                    hi,
                    chunk_count,
                    minimum,
                    maximum,
                )

        equal_time, remainder = divmod(stage * timeline.duration, chunk_count)
        for value in (equal_time, equal_time + int(remainder > 0)):
            _add_hard_candidate(
                candidates,
                timeline,
                stage,
                value,
                chunk_count,
                minimum,
                maximum,
            )
        equal_speech, remainder = divmod(stage * timeline.total_speech, chunk_count)
        for speech in (equal_speech, equal_speech + int(remainder > 0)):
            _add_hard_candidate(
                candidates,
                timeline,
                stage,
                timeline.hard_coordinate_at_cumulative(speech),
                chunk_count,
                minimum,
                maximum,
            )

    # 如果硬切点总数不大，直接穷举所有可能的硬切点
    if hard_span <= _EXHAUSTIVE_HARD_POINT_LIMIT:
        for stage in range(1, chunk_count):
            exhaustive: set[int] = set()
            window_lo, window_hi = _stage_window(
                timeline.duration,
                chunk_count,
                stage,
                minimum,
                maximum,
            )
            for hard_lo, hard_hi in feasible_hard_ranges[stage]:
                lo = max(window_lo, hard_lo)
                hi = min(window_hi, hard_hi)
                if lo <= hi:
                    exhaustive.update(range(lo, hi + 1))
            candidates[stage] = exhaustive
        return candidates

    maximum_run = hard_cut_count + 1
    for left_stage in range(chunk_count):
        right_limit = min(chunk_count, left_stage + maximum_run)
        for right_stage in range(left_stage + 2, right_limit + 1):
            chunks_between = right_stage - left_stage
            for left_node in safe_nodes[left_stage]:
                for right_node in safe_nodes[right_stage]:
                    if right_node.speech < left_node.speech:
                        continue
                    if (
                        right_node.speech - left_node.speech
                        > chunks_between * maximum_load
                    ):
                        continue
                    if right_node.hi - left_node.lo < chunks_between * minimum:
                        continue
                    if right_node.lo - left_node.hi > chunks_between * maximum:
                        continue
                    speech_delta = right_node.speech - left_node.speech
                    for offset in range(1, chunks_between):
                        stage = left_stage + offset
                        if not feasible_hard_ranges[stage]:
                            continue
                        quotient, remainder = divmod(
                            left_node.speech * chunks_between + speech_delta * offset,
                            chunks_between,
                        )
                        for speech in (quotient, quotient + int(remainder > 0)):
                            _add_hard_candidate(
                                candidates,
                                timeline,
                                stage,
                                timeline.hard_coordinate_at_cumulative(speech),
                                chunk_count,
                                minimum,
                                maximum,
                            )

    for source_stage, stage_nodes in enumerate(safe_nodes):
        for node in stage_nodes:
            for distance in range(1, maximum_run + 1):
                for target_stage, direction in (
                    (source_stage + distance, 1),
                    (source_stage - distance, -1),
                ):
                    if not 0 < target_stage < chunk_count:
                        continue
                    if not feasible_hard_ranges[target_stage]:
                        continue
                    for endpoint in (node.lo, node.hi):
                        for chunk_limit in (minimum, maximum):
                            _add_hard_candidate(
                                candidates,
                                timeline,
                                target_stage,
                                endpoint + direction * distance * chunk_limit,
                                chunk_count,
                                minimum,
                                maximum,
                            )
                    for speech in (node.speech + direction * distance * maximum_load,):
                        _add_hard_candidate(
                            candidates,
                            timeline,
                            target_stage,
                            timeline.hard_coordinate_at_cumulative(speech),
                            chunk_count,
                            minimum,
                            maximum,
                        )

    for stage in range(1, chunk_count):
        neighbors = {value + delta for value in candidates[stage] for delta in (-1, 1)}
        for value in neighbors:
            _add_hard_candidate(
                candidates,
                timeline,
                stage,
                value,
                chunk_count,
                minimum,
                maximum,
            )
        candidates[stage] = {
            value
            for value in candidates[stage]
            if any(lo <= value <= hi for lo, hi in feasible_hard_ranges[stage])
        }
    return candidates


def _optimize_over_event_nodes(
    timeline: Timeline,
    chunk_count: int,
    minimum: int,
    maximum: int,
    hard_cut_count: int,
    maximum_load: int,
    safe_nodes: list[list[_BoundaryNode]],
    hard_candidates: list[set[int]],
) -> BoundaryOptimizationResult | None:
    nodes_by_stage: list[list[_BoundaryNode]] = [safe_nodes[0]]
    for stage in range(1, chunk_count):
        nodes = list(safe_nodes[stage])
        nodes.extend(
            _BoundaryNode(
                ("hard", stage, value),
                value,
                value,
                timeline.speech_at(value),
                1,
            )
            for value in sorted(hard_candidates[stage])
        )
        nodes.sort(key=lambda node: (node.lo, node.hi, node.hard))
        nodes_by_stage.append(nodes)
    nodes_by_stage.append(safe_nodes[-1])

    start = safe_nodes[0][0]
    labels = [_PathLabel(start, 0, 0, 0, 0, (), ())]
    for stage in range(1, chunk_count):
        grouped: dict[tuple[tuple[object, ...], int], list[_PathLabel]] = {}
        for previous in labels:
            earliest = previous.reachable_lo + minimum
            latest = previous.reachable_hi + maximum
            for node in nodes_by_stage[stage]:
                if node.hi < earliest:
                    continue
                if node.lo > latest:
                    break
                load = node.speech - previous.node.speech
                if load < 0 or load > maximum_load:
                    continue
                hard_used = previous.hard_used + node.hard
                if hard_used > hard_cut_count:
                    continue
                reachable_lo = max(node.lo, earliest)
                reachable_hi = min(node.hi, latest)
                if reachable_lo > reachable_hi:
                    continue
                label = _PathLabel(
                    node=node,
                    reachable_lo=reachable_lo,
                    reachable_hi=reachable_hi,
                    hard_used=hard_used,
                    square_sum=previous.square_sum + load * load,
                    path=(*previous.path, node),
                    path_key=(*previous.path_key, (node.lo, node.hi, node.hard)),
                )
                grouped.setdefault((node.key, hard_used), []).append(label)
        labels = [
            label for group in grouped.values() for label in _prune_path_labels(group)
        ]
        if not labels:
            return None

    final_candidates: list[tuple[int, tuple[int, ...]]] = []
    for label in labels:
        if label.hard_used != hard_cut_count:
            continue
        load = timeline.total_speech - label.node.speech
        if load < 0 or load > maximum_load:
            continue
        if max(label.reachable_lo, timeline.duration - maximum) > min(
            label.reachable_hi,
            timeline.duration - minimum,
        ):
            continue
        boundaries = _reconstruct_earliest_boundaries(
            label.path,
            timeline.duration,
            minimum,
            maximum,
        )
        if boundaries is None:
            continue
        final_candidates.append((label.square_sum + load * load, boundaries))
    if not final_candidates:
        return None
    _square_sum, boundaries = min(final_candidates, key=lambda item: (item[0], item[1]))
    return _result_from_boundaries(timeline, boundaries)


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

    hard_cut_count, _hard_layers = _minimum_hard_cut_count(
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
    safe_nodes = _safe_nodes_by_stage(
        timeline,
        chunk_count,
        min_chunk_samples,
        max_chunk_samples,
    )
    feasible_hard_ranges = _hard_feasible_ranges_by_stage(
        load_layers,
        timeline,
        chunk_count,
        min_chunk_samples,
        max_chunk_samples,
        hard_cut_count,
        maximum_load,
    )
    hard_candidates = _hard_candidates_by_stage(
        timeline,
        chunk_count,
        min_chunk_samples,
        max_chunk_samples,
        hard_cut_count,
        maximum_load,
        safe_nodes,
        feasible_boundaries,
        feasible_hard_ranges,
    )
    optimized = _optimize_over_event_nodes(
        timeline,
        chunk_count,
        min_chunk_samples,
        max_chunk_samples,
        hard_cut_count,
        maximum_load,
        safe_nodes,
        hard_candidates,
    )
    if optimized is None:
        return _result_from_boundaries(timeline, feasible_boundaries)
    if (
        optimized.hard_cut_count != hard_cut_count
        or optimized.max_speech_load != maximum_load
    ):
        raise RuntimeError("Event optimizer violated the fixed lexicographic costs.")
    return optimized
