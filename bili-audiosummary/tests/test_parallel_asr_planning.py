import math
import sys
from itertools import combinations, pairwise
from time import perf_counter
from types import ModuleType

import numpy as np
import pytest

from scripts.asr import parallel as parallel_asr
from scripts.asr.chunking import NormalizedAudio, optimizer
from scripts.asr.chunking.optimizer import optimize_chunk_boundaries
from scripts.asr.parallel import media
from scripts.runtime_options import TranscribeOptions


def make_source(duration: float) -> parallel_asr.AsrSourceAudio:
    return parallel_asr.AsrSourceAudio(
        path="audio.m4a", size=123, mtime=456.0, duration=duration
    )


def make_plan(
    duration: float,
    *,
    cpu_count: int | None = 32,
    speech_intervals: list[tuple[float, float]] | None = None,
    num_workers: int | None = None,
    cpu_threads: int | None = None,
    max_chunk_seconds: float | None = None,
):
    return parallel_asr.build_parallel_asr_plan(
        duration_seconds=duration,
        cpu_count=cpu_count,
        source_audio=make_source(duration),
        options=TranscribeOptions(
            model="model-dir",
            language="zh",
            num_workers=num_workers,
            cpu_threads=cpu_threads,
            max_chunk_seconds=max_chunk_seconds,
        ),
        speech_intervals=speech_intervals or [],
    )


def chunk_ends(plan) -> list[float]:
    return [round(chunk.start + chunk.duration, 3) for chunk in plan.chunks]


def assert_valid_layout(plan, duration: float) -> None:
    assert plan.chunks
    assert plan.chunks[0].start == 0.0
    assert chunk_ends(plan)[-1] == pytest.approx(duration, abs=0.001)
    for index, chunk in enumerate(plan.chunks):
        assert chunk.index == index
        assert chunk.duration <= plan.planning_parameters.max_chunk_seconds
        if duration >= 30.0:
            assert chunk.duration >= 30.0
        if index:
            previous = plan.chunks[index - 1]
            assert chunk.start == pytest.approx(
                previous.start + previous.duration, abs=0.001
            )
    assert len(plan.chunks) % plan.num_workers == 0
    assert len(plan.chunks) // plan.num_workers == plan.batch_count
    assert plan.chunks[-1].end_boundary == "audio_end"


def test_detect_speech_intervals_uses_pinned_silero_vad_parameters(monkeypatch) -> None:
    calls: dict[str, object] = {}
    vad = ModuleType("faster_whisper.vad")

    class FakeVadOptions:
        def __init__(self, **kwargs) -> None:
            calls["vad_options"] = kwargs

    def fake_get_speech_timestamps(audio, options, sampling_rate=16000):
        calls["vad_call"] = (audio, options, sampling_rate)
        return [{"start": 0, "end": 16_000}]

    vad.VadOptions = FakeVadOptions  # type: ignore[attr-defined]
    vad.get_speech_timestamps = fake_get_speech_timestamps  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad)

    audio = NormalizedAudio(np.zeros(16_000, dtype=np.float32))
    assert media.detect_speech_intervals(audio) == [(0, 16_000)]
    assert calls["vad_call"][0] is audio.samples
    assert calls["vad_options"] == {
        "threshold": 0.35,
        "neg_threshold": 0.25,
        "min_speech_duration_ms": 0,
        "min_silence_duration_ms": 300,
        "max_speech_duration_s": math.inf,
        "speech_pad_ms": 0,
    }


@pytest.mark.parametrize("duration", [30.0, 180.0, 301.0, 900.0, 3600.0])
def test_planned_chunks_cover_audio_once_with_strict_duration_bounds(duration) -> None:
    plan = make_plan(duration)
    assert plan.schema_version == 6
    assert_valid_layout(plan, duration)


def test_audio_shorter_than_thirty_seconds_is_single_worker_single_chunk() -> None:
    plan = make_plan(29.999)
    assert plan.num_workers == 1
    assert len(plan.chunks) == 1
    assert plan.chunks[0].duration == 29.999


def test_automatic_workers_are_capped_by_required_chunk_count() -> None:
    short = make_plan(62.0, cpu_count=32)
    long = make_plan(900.0, cpu_count=32)
    assert (short.num_workers, short.cpu_threads, len(short.chunks)) == (1, 24, 1)
    assert (long.num_workers, long.cpu_threads, len(long.chunks)) == (4, 6, 8)


@pytest.mark.parametrize(
    ("num_workers", "cpu_threads", "expected_workers", "expected_threads"),
    [(5, 4, 5, 4), (7, None, 7, 3), (None, 5, 4, 5)],
)
def test_explicit_worker_parameters_keep_precedence(
    num_workers, cpu_threads, expected_workers, expected_threads
) -> None:
    plan = make_plan(
        900.0, num_workers=num_workers, cpu_threads=cpu_threads, max_chunk_seconds=180
    )
    assert (plan.num_workers, plan.cpu_threads) == (expected_workers, expected_threads)
    assert_valid_layout(plan, 900.0)


@pytest.mark.parametrize(
    ("duration", "workers", "threads"),
    [(900.0, 5, 5), (29.5, 2, None), (900.0, None, 25), (900.0, 0, None)],
)
def test_invalid_explicit_worker_configuration_is_rejected(duration, workers, threads):
    with pytest.raises(ValueError):
        make_plan(duration, num_workers=workers, cpu_threads=threads)


def test_no_speech_uses_any_silence_position_without_hard_cuts() -> None:
    plan = make_plan(900.0, speech_intervals=[])
    assert len(plan.chunks) == 8
    assert [chunk.end_boundary for chunk in plan.chunks[:-1]] == ["silence"] * 7
    assert plan.hard_cut_count == 0


def test_long_continuous_speech_uses_minimum_required_hard_cuts() -> None:
    plan = make_plan(900.0, speech_intervals=[(0.0, 900.0)])
    assert len(plan.chunks) == 8
    assert [chunk.end_boundary for chunk in plan.chunks[:-1]] == ["hard"] * 7
    assert plan.hard_cut_count == 7


def test_leading_trailing_and_internal_silence_are_full_legal_windows() -> None:
    plan = make_plan(
        240.0,
        num_workers=3,
        speech_intervals=[(40.0, 100.0), (140.0, 200.0)],
    )
    assert len(plan.chunks) == 3
    assert all(chunk.end_boundary != "hard" for chunk in plan.chunks)
    assert_valid_layout(plan, 240.0)


def test_hard_cut_count_has_priority_over_batch_count() -> None:
    plan = make_plan(
        360.0,
        num_workers=1,
        max_chunk_seconds=180,
        speech_intervals=[(0.0, 170.0), (190.0, 360.0)],
    )
    assert len(plan.chunks) == 2
    assert plan.hard_cut_count == 0


def test_batch_count_has_priority_over_speech_load_balance() -> None:
    plan = make_plan(
        150.0,
        num_workers=1,
        speech_intervals=[(0.0, 140.0)],
    )
    assert len(plan.chunks) == 1
    assert plan.batch_count == 1


def _oracle(duration, count, speech, minimum, maximum):
    def speech_at(value):
        return sum(max(0, min(value, end) - start) for start, end in speech)

    candidates = []
    for internal in combinations(range(1, duration), count - 1):
        boundaries = (0, *internal, duration)
        lengths = [b - a for a, b in pairwise(boundaries)]
        if not all(minimum <= value <= maximum for value in lengths):
            continue
        loads = [speech_at(b) - speech_at(a) for a, b in pairwise(boundaries)]
        hard = sum(any(a < point < b for a, b in speech) for point in internal)
        total = sum(loads)
        msre = (
            sum((count * load - total) ** 2 for load in loads) / (count * total * total)
            if total
            else 0.0
        )
        candidates.append(((hard, max(loads), msre, boundaries), boundaries))
    return min(candidates)[1]


@pytest.mark.parametrize(
    "speech",
    [(), ((0, 12),), ((0, 3), (5, 7), (9, 12)), ((8, 12), (0, 4))],
)
def test_optimizer_matches_exhaustive_lexicographic_oracle(speech) -> None:
    result = optimize_chunk_boundaries(
        duration_samples=12,
        chunk_count=3,
        speech_intervals=speech,
        min_chunk_samples=3,
        max_chunk_samples=6,
    )
    assert result.boundaries == _oracle(12, 3, speech, 3, 6)


def _intervals_from_mask(mask: int, duration: int) -> tuple[tuple[int, int], ...]:
    intervals = []
    start = None
    for coordinate in range(duration + 1):
        is_speech = coordinate < duration and mask & (1 << coordinate)
        if is_speech and start is None:
            start = coordinate
        elif not is_speech and start is not None:
            intervals.append((start, coordinate))
            start = None
    return tuple(intervals)


def test_event_optimizer_matches_exhaustive_oracle(monkeypatch) -> None:
    monkeypatch.setattr(optimizer, "_EXHAUSTIVE_STATE_LIMIT", 0)

    for mask in range(1, 2**12 - 1, 37):
        speech = _intervals_from_mask(mask, 12)
        result = optimize_chunk_boundaries(
            duration_samples=12,
            chunk_count=3,
            speech_intervals=speech,
            min_chunk_samples=3,
            max_chunk_samples=6,
        )

        assert result.boundaries == _oracle(12, 3, speech, 3, 6), hex(mask)


def test_event_optimizer_is_invariant_under_coordinate_scaling(monkeypatch) -> None:
    monkeypatch.setattr(optimizer, "_EXHAUSTIVE_STATE_LIMIT", 0)
    speech = ((0, 3), (5, 7), (9, 12))
    baseline = optimize_chunk_boundaries(
        duration_samples=12,
        chunk_count=3,
        speech_intervals=speech,
        min_chunk_samples=3,
        max_chunk_samples=6,
    )
    scale = 100
    scaled = optimize_chunk_boundaries(
        duration_samples=12 * scale,
        chunk_count=3,
        speech_intervals=tuple((start * scale, end * scale) for start, end in speech),
        min_chunk_samples=3 * scale,
        max_chunk_samples=6 * scale,
    )

    assert scaled.boundaries == tuple(value * scale for value in baseline.boundaries)
    assert scaled.speech_loads == tuple(
        value * scale for value in baseline.speech_loads
    )
    assert scaled.hard_cut_count == baseline.hard_cut_count
    assert scaled.speech_load_msre == pytest.approx(baseline.speech_load_msre)


def test_reversing_and_overlapping_speech_input_produces_same_plan() -> None:
    intervals = [(0.0, 80.0), (70.0, 160.0), (200.0, 360.0)]
    assert make_plan(360.0, speech_intervals=intervals) == make_plan(
        360.0, speech_intervals=list(reversed(intervals))
    )


def test_dense_boundaries_scale_to_one_hour_under_eight_seconds() -> None:
    intervals = [(float(start), float(start + 2)) for start in range(0, 3600, 3)]
    started = perf_counter()
    plan = make_plan(3600.0, cpu_count=64, speech_intervals=intervals)
    # Sample-coordinate arithmetic preserves exact PCM boundaries and is still
    # bounded to a small planning-only fraction of an hour-long ASR run.
    assert perf_counter() - started < 8.0
    assert_valid_layout(plan, 3600.0)


def test_plan_records_effective_vad_and_planning_parameters() -> None:
    plan = make_plan(900.0, max_chunk_seconds=180)
    assert plan.vad_parameters == parallel_asr.VadParameters(
        threshold=0.35,
        neg_threshold=0.25,
        min_speech_duration_ms=0,
        min_silence_duration_ms=300,
        max_speech_duration_s=None,
        speech_pad_ms=0,
        sampling_rate=16_000,
    )
    assert plan.planning_parameters == parallel_asr.PlanningParameters(
        min_chunk_seconds=30.0, max_chunk_seconds=180.0
    )
