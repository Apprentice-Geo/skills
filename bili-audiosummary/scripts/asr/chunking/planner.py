from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal

from scripts.asr.chunking.optimizer import optimize_chunk_boundaries
from scripts.asr.chunking.types import (
    BOUNDARY_AUDIO_END,
    BOUNDARY_HARD,
    BOUNDARY_SILENCE,
    BOUNDARY_TYPES,
    MAX_CHUNK_SAMPLES,
    MIN_CHUNK_SAMPLES,
    ChunkLayout,
)

CountStrategy = Literal["divisible", "full"]


@dataclass(frozen=True)
class PlanningParameters:
    min_chunk_samples: int = MIN_CHUNK_SAMPLES
    max_chunk_samples: int = MAX_CHUNK_SAMPLES

    @property
    def min_chunk_seconds(self) -> float:
        return self.min_chunk_samples / 16_000

    @property
    def max_chunk_seconds(self) -> float:
        return self.max_chunk_samples / 16_000


DEFAULT_PLANNING_PARAMETERS = PlanningParameters()


def legal_chunk_count_range(
    sample_count: int,
    parameters: PlanningParameters = DEFAULT_PLANNING_PARAMETERS,
) -> tuple[int, int]:
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count <= 0
    ):
        raise ValueError(f"Invalid audio sample count: {sample_count!r}.")
    if sample_count < parameters.min_chunk_samples:
        return 1, 1
    minimum = math.ceil(sample_count / parameters.max_chunk_samples)
    maximum = sample_count // parameters.min_chunk_samples
    if minimum > maximum:
        raise ValueError("Unable to split audio within the configured chunk bounds.")
    return minimum, maximum


def candidate_chunk_counts(
    sample_count: int,
    *,
    group_size: int,
    count_strategy: CountStrategy,
    parameters: PlanningParameters = DEFAULT_PLANNING_PARAMETERS,
) -> list[int]:
    if (
        isinstance(group_size, bool)
        or not isinstance(group_size, int)
        or group_size < 1
    ):
        raise ValueError("group_size must be a positive integer.")
    minimum, maximum = legal_chunk_count_range(sample_count, parameters)
    if minimum == maximum == 1 and sample_count < parameters.min_chunk_samples:
        return [1]
    divisible = [
        count for count in range(minimum, maximum + 1) if count % group_size == 0
    ]
    if count_strategy == "divisible":
        return divisible
    if count_strategy == "full":
        return divisible or [maximum]
    raise ValueError(f"Unknown chunk count strategy: {count_strategy!r}.")


def _normalize_speech_samples(
    sample_count: int,
    intervals: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    normalized = []
    for start, end in intervals:
        if isinstance(start, bool) or isinstance(end, bool):
            raise ValueError("Speech sample coordinates must be integers.")
        start_value = max(0, min(sample_count, int(start)))
        end_value = max(0, min(sample_count, int(end)))
        if end_value < start_value:
            raise ValueError(f"Invalid speech interval: {(start, end)!r}.")
        if end_value > start_value:
            normalized.append((start_value, end_value))
    return tuple(normalized)


def validate_layouts(
    layouts: Iterable[ChunkLayout],
    sample_count: int,
    parameters: PlanningParameters = DEFAULT_PLANNING_PARAMETERS,
) -> tuple[ChunkLayout, ...]:
    chunks = tuple(layouts)
    if not chunks:
        raise ValueError("Chunk layouts must not be empty.")
    short_audio = sample_count < parameters.min_chunk_samples
    previous_end = 0
    for index, chunk in enumerate(chunks):
        if chunk.index != index or chunk.start_sample != previous_end:
            raise ValueError("Chunks must continuously cover the source audio.")
        length = chunk.sample_count
        if length <= 0 or length > parameters.max_chunk_samples:
            raise ValueError("Invalid chunk sample count.")
        if not short_audio and length < parameters.min_chunk_samples:
            raise ValueError("Invalid chunk sample count.")
        if not 0 <= chunk.estimated_speech_samples <= length:
            raise ValueError("Invalid estimated speech sample count.")
        if chunk.end_boundary not in BOUNDARY_TYPES:
            raise ValueError("Invalid chunk boundary.")
        expected_final = index == len(chunks) - 1
        if (chunk.end_boundary == BOUNDARY_AUDIO_END) != expected_final:
            raise ValueError("Only the final chunk may end at audio_end.")
        previous_end = chunk.end_sample
    if previous_end != sample_count:
        raise ValueError("Chunks do not cover the complete source audio.")
    return chunks


def plan_fixed_chunk_count(
    sample_count: int,
    chunk_count: int,
    speech_intervals: Iterable[tuple[int, int]],
    parameters: PlanningParameters = DEFAULT_PLANNING_PARAMETERS,
) -> tuple[ChunkLayout, ...]:
    minimum, maximum = legal_chunk_count_range(sample_count, parameters)
    if not minimum <= chunk_count <= maximum:
        raise ValueError("Chunk count is outside the legal range.")
    intervals = _normalize_speech_samples(sample_count, speech_intervals)
    optimized = optimize_chunk_boundaries(
        duration_samples=sample_count,
        chunk_count=chunk_count,
        speech_intervals=intervals,
        min_chunk_samples=min(sample_count, parameters.min_chunk_samples),
        max_chunk_samples=parameters.max_chunk_samples,
    )
    layouts = []
    for index, (start, end, speech) in enumerate(
        zip(
            optimized.boundaries[:-1],
            optimized.boundaries[1:],
            optimized.speech_loads,
            strict=True,
        )
    ):
        boundary = BOUNDARY_AUDIO_END
        if index < chunk_count - 1:
            boundary = (
                BOUNDARY_HARD
                if any(left < end < right for left, right in intervals)
                else BOUNDARY_SILENCE
            )
        layouts.append(ChunkLayout(index, start, end, boundary, speech))
    return validate_layouts(layouts, sample_count, parameters)


def plan_chunks(
    sample_count: int,
    speech_intervals: Iterable[tuple[int, int]],
    *,
    group_size: int,
    count_strategy: CountStrategy,
    parameters: PlanningParameters = DEFAULT_PLANNING_PARAMETERS,
    fixed_chunk_count: int | None = None,
) -> tuple[ChunkLayout, ...]:
    if fixed_chunk_count is not None:
        return plan_fixed_chunk_count(
            sample_count,
            fixed_chunk_count,
            speech_intervals,
            parameters,
        )
    intervals = tuple(speech_intervals)
    candidates = candidate_chunk_counts(
        sample_count,
        group_size=group_size,
        count_strategy=count_strategy,
        parameters=parameters,
    )
    ranked = []
    for count in candidates:
        layouts = plan_fixed_chunk_count(sample_count, count, intervals, parameters)
        loads = tuple(item.estimated_speech_samples for item in layouts)
        mean = sum(loads) / len(loads)
        msre = (
            sum(((value - mean) / mean) ** 2 for value in loads) / len(loads)
            if mean
            else 0.0
        )
        rank = (
            sum(item.end_boundary == BOUNDARY_HARD for item in layouts),
            math.ceil(count / group_size),
            max(loads),
            round(msre, 15),
            tuple(item.start_sample for item in layouts) + (layouts[-1].end_sample,),
        )
        ranked.append((rank, layouts))
        if rank[0] == 0:
            break
    if not ranked:
        raise ValueError("Unable to build a valid chunk layout.")
    return min(ranked, key=lambda item: item[0])[1]
