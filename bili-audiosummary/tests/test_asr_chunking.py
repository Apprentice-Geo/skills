from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from scripts.asr.chunking import (
    MAX_CHUNK_SAMPLES,
    SAMPLE_RATE,
    NormalizedAudio,
    PlanningParameters,
    candidate_chunk_counts,
    decode_normalized_audio,
    plan_chunks,
    plan_fixed_chunk_count,
)


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
