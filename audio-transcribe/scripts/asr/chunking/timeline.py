from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Timeline:
    duration: int
    speech_intervals: tuple[tuple[int, int], ...]
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    prefix_speech: tuple[int, ...]
    cumulative_ends: tuple[int, ...]
    safe_ranges: tuple[tuple[int, int], ...]
    hard_ranges: tuple[tuple[int, int], ...]
    total_speech: int

    @classmethod
    def build(
        cls,
        duration: int,
        speech_intervals: Iterable[tuple[int, int]],
    ) -> Timeline:
        normalized = normalize_speech_intervals(duration, speech_intervals)
        starts = tuple(start for start, _end in normalized)
        ends = tuple(end for _start, end in normalized)
        prefix: list[int] = []
        cumulative_ends: list[int] = []
        total = 0
        for start, end in normalized:
            prefix.append(total)
            total += end - start
            cumulative_ends.append(total)

        safe: list[tuple[int, int]] = []
        hard: list[tuple[int, int]] = []
        previous_end = 0
        for start, end in normalized:
            safe.append((previous_end, start))
            if start + 1 <= end - 1:
                hard.append((start + 1, end - 1))
            previous_end = end
        safe.append((previous_end, duration))
        return cls(
            duration=duration,
            speech_intervals=normalized,
            starts=starts,
            ends=ends,
            prefix_speech=tuple(prefix),
            cumulative_ends=tuple(cumulative_ends),
            safe_ranges=tuple(safe),
            hard_ranges=tuple(hard),
            total_speech=total,
        )

    def speech_at(self, coordinate: int) -> int:
        if not self.starts or coordinate <= 0:
            return 0
        if coordinate >= self.duration:
            return self.total_speech
        index = bisect_right(self.starts, coordinate) - 1
        if index < 0:
            return 0
        start = self.starts[index]
        end = self.ends[index]
        return self.prefix_speech[index] + max(0, min(coordinate, end) - start)

    def is_hard(self, coordinate: int) -> bool:
        index = bisect_right(self.starts, coordinate) - 1
        return index >= 0 and self.starts[index] < coordinate < self.ends[index]

    def earliest_coordinate_with_speech_at_least(self, speech_samples: int) -> int:
        if speech_samples <= 0:
            return 0
        if speech_samples > self.total_speech:
            return self.duration + 1
        index = bisect_left(self.cumulative_ends, speech_samples)
        start, _end = self.speech_intervals[index]
        return start + speech_samples - self.prefix_speech[index]

    def latest_coordinate_with_speech_at_most(self, speech_samples: int) -> int:
        if speech_samples < 0:
            return -1
        if speech_samples >= self.total_speech:
            return self.duration
        index = bisect_right(self.cumulative_ends, speech_samples)
        start, _end = self.speech_intervals[index]
        return start + speech_samples - self.prefix_speech[index]

    def hard_coordinate_at_cumulative(self, speech_samples: int) -> int | None:
        if speech_samples <= 0 or speech_samples >= self.total_speech:
            return None
        index = bisect_right(self.cumulative_ends, speech_samples)
        if index >= len(self.speech_intervals):
            return None
        start, end = self.speech_intervals[index]
        prefix = self.prefix_speech[index]
        coordinate = start + speech_samples - prefix
        if start < coordinate < end:
            return coordinate
        return None


def normalize_speech_intervals(
    duration: int,
    speech_intervals: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    normalized: list[tuple[int, int]] = []
    for raw_start, raw_end in speech_intervals:
        if isinstance(raw_start, bool) or isinstance(raw_end, bool):
            raise ValueError(f"Invalid speech interval: {(raw_start, raw_end)!r}.")
        start = int(raw_start)
        end = int(raw_end)
        if start != raw_start or end != raw_end or end < start:
            raise ValueError(f"Invalid speech interval: {(raw_start, raw_end)!r}.")
        start = max(0, min(duration, start))
        end = max(0, min(duration, end))
        if end > start:
            normalized.append((start, end))
    normalized.sort()

    merged: list[list[int]] = []
    for start, end in normalized:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)
