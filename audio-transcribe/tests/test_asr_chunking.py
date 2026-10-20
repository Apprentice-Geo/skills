from __future__ import annotations

from itertools import combinations, pairwise
from pathlib import Path

import numpy as np
import pytest

from scripts.asr.chunking import (
    DEFAULT_VAD_PARAMETERS,
    MAX_CHUNK_SAMPLES,
    SAMPLE_RATE,
    NormalizedAudio,
    PlanningParameters,
    candidate_chunk_counts,
    decode_normalized_audio,
    detect_speech_samples,
    plan_chunks,
    plan_fixed_chunk_count,
)
from scripts.asr.chunking.optimizer import optimize_chunk_boundaries


def _complete_boundary_oracle(
    *,
    duration_samples: int,
    chunk_count: int,
    min_chunk_samples: int,
    max_chunk_samples: int,
    speech_intervals: list[tuple[int, int]],
) -> tuple[int, int]:
    speech_mask = [False] * duration_samples
    for start, end in speech_intervals:
        for sample in range(start, end):
            speech_mask[sample] = True

    candidates: list[tuple[int, int]] = []
    for internal in combinations(range(1, duration_samples), chunk_count - 1):
        boundaries = (0, *internal, duration_samples)
        durations = tuple(end - start for start, end in pairwise(boundaries))
        if not all(
            min_chunk_samples <= duration <= max_chunk_samples for duration in durations
        ):
            continue
        loads = tuple(
            sum(speech_mask[start:end]) for start, end in pairwise(boundaries)
        )
        hard_cut_count = sum(
            speech_mask[boundary - 1] and speech_mask[boundary] for boundary in internal
        )
        candidates.append((hard_cut_count, max(loads)))

    return min(candidates)


def test_decode_normalizes_once_to_mono_float32(
    monkeypatch, workspace_tmp_path: Path
) -> None:
    calls = []

    def fake_decode(path: str, *, sampling_rate: int):
        calls.append((path, sampling_rate))
        return np.arange(32, dtype=np.float64)

    monkeypatch.setattr("faster_whisper.decode_audio", fake_decode)
    audio = decode_normalized_audio(workspace_tmp_path / "input.m4a")

    assert calls == [
        (str(workspace_tmp_path / "input.m4a").replace("\\", "/"), SAMPLE_RATE)
    ]
    assert audio.samples.dtype == np.float32
    assert audio.samples.ndim == 1
    assert audio.sample_rate == SAMPLE_RATE
    assert audio.sample_count == 32


def test_detect_speech_samples_reads_faster_whisper_timestamps(monkeypatch) -> None:
    monkeypatch.setattr(
        "faster_whisper.vad.get_speech_timestamps",
        lambda *_args, **_kwargs: [{"start": 10, "end": 20}],
    )

    assert detect_speech_samples(
        NormalizedAudio(np.zeros(32, dtype=np.float32)), DEFAULT_VAD_PARAMETERS
    ) == [(10, 20)]


def test_layout_slices_are_zero_copy_and_cover_every_sample() -> None:
    audio = NormalizedAudio(np.arange(1_920_001, dtype=np.float32))
    chunks = plan_fixed_chunk_count(audio.sample_count, 2, [])
    slices = [audio.slice(chunk) for chunk in chunks]

    assert chunks[0].start_sample == 0
    assert chunks[-1].end_sample == audio.sample_count
    assert all(
        left.end_sample == right.start_sample for left, right in pairwise(chunks)
    )
    assert sum(item.size for item in slices) == audio.sample_count
    assert all(np.shares_memory(audio.samples, item) for item in slices)
    assert (
        max(chunk.end_sample - chunk.start_sample for chunk in chunks)
        <= MAX_CHUNK_SAMPLES
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(25, [1]), (60, [2]), (100, [3]), (119, [3]), (120, [4]), (180, [4])],
)
def test_full_policy_is_driven_by_group_size(seconds: int, expected: list[int]) -> None:
    assert (
        candidate_chunk_counts(
            seconds * SAMPLE_RATE,
            group_size=4,
            count_strategy="full",
        )
        == expected
    )


def test_fixed_chunk_count_has_provider_independent_boundaries() -> None:
    sample_count = 357 * SAMPLE_RATE
    speech = [
        (10 * SAMPLE_RATE, 90 * SAMPLE_RATE),
        (130 * SAMPLE_RATE, 340 * SAMPLE_RATE),
    ]

    whisper = plan_chunks(
        sample_count,
        speech,
        group_size=4,
        count_strategy="divisible",
        fixed_chunk_count=8,
    )
    qwen = plan_chunks(
        sample_count,
        speech,
        group_size=4,
        count_strategy="full",
        fixed_chunk_count=8,
    )

    assert whisper == qwen


def test_planner_rejects_non_contiguous_or_oversized_layouts() -> None:
    parameters = PlanningParameters()
    with pytest.raises(ValueError):
        plan_fixed_chunk_count(MAX_CHUNK_SAMPLES + 1, 1, [], parameters)


@pytest.mark.parametrize(
    (
        "duration_samples",
        "chunk_count",
        "min_chunk_samples",
        "max_chunk_samples",
        "speech_intervals",
    ),
    [
        (8, 2, 2, 6, []),
        (8, 2, 2, 6, [(0, 8)]),
        (12, 3, 2, 6, [(2, 4), (7, 11)]),
        (14, 3, 3, 6, [(0, 5), (9, 14)]),
        (15, 4, 2, 5, [(1, 8), (10, 15)]),
        (18, 4, 3, 6, [(2, 7), (9, 17)]),
    ],
)
def test_boundary_optimizer_matches_complete_combination_oracle(
    duration_samples: int,
    chunk_count: int,
    min_chunk_samples: int,
    max_chunk_samples: int,
    speech_intervals: list[tuple[int, int]],
) -> None:
    expected_score = _complete_boundary_oracle(
        duration_samples=duration_samples,
        chunk_count=chunk_count,
        min_chunk_samples=min_chunk_samples,
        max_chunk_samples=max_chunk_samples,
        speech_intervals=speech_intervals,
    )

    result = optimize_chunk_boundaries(
        duration_samples=duration_samples,
        chunk_count=chunk_count,
        min_chunk_samples=min_chunk_samples,
        max_chunk_samples=max_chunk_samples,
        speech_intervals=speech_intervals,
    )

    assert (result.hard_cut_count, result.max_speech_load) == expected_score
    assert result.boundaries[0] == 0
    assert result.boundaries[-1] == duration_samples
    assert all(
        min_chunk_samples <= end - start <= max_chunk_samples
        for start, end in pairwise(result.boundaries)
    )
    assert len(result.speech_loads) == chunk_count
    assert result == optimize_chunk_boundaries(
        duration_samples=duration_samples,
        chunk_count=chunk_count,
        min_chunk_samples=min_chunk_samples,
        max_chunk_samples=max_chunk_samples,
        speech_intervals=speech_intervals,
    )


def test_boundary_optimizer_is_exact_for_all_eight_sample_speech_masks() -> None:
    for mask in range(1 << 8):
        # 单样本区间同时覆盖相邻语音合并与多段静音的边界情形。
        speech_intervals = [
            (index, index + 1) for index in range(8) if mask >> index & 1
        ]
        for chunk_count in (2, 3):
            test_boundary_optimizer_matches_complete_combination_oracle(
                duration_samples=8,
                chunk_count=chunk_count,
                min_chunk_samples=2,
                max_chunk_samples=5,
                speech_intervals=speech_intervals,
            )
