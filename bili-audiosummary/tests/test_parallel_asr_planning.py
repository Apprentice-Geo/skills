import math
import sys
from pathlib import Path
from time import perf_counter
from types import ModuleType

import pytest

from scripts.asr import parallel as parallel_asr
from scripts.asr.parallel import media
from scripts.runtime_options import TranscribeOptions


def make_source(duration: float) -> parallel_asr.AsrSourceAudio:
    return parallel_asr.AsrSourceAudio(
        path="audio.m4a",
        size=123,
        mtime=456.0,
        duration=duration,
    )


def make_plan(
    duration: float,
    *,
    cpu_count: int | None = 32,
    speech_intervals: list[tuple[float, float]] | None = None,
    num_workers: int | None = None,
    cpu_threads: int | None = None,
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
        assert chunk.duration <= 300.0
        if duration >= 60.0:
            assert chunk.duration >= 60.0
        if index:
            previous = plan.chunks[index - 1]
            assert chunk.start == pytest.approx(
                previous.start + previous.duration,
                abs=0.001,
            )
    assert len(plan.chunks) % plan.num_workers == 0
    assert plan.chunks[-1].end_boundary == "audio_end"


def speech_intervals_for_cut_points(
    duration: float,
    cut_points: list[float],
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    start = 0.0
    for cut in cut_points:
        intervals.append((start, cut - 10.0))
        start = cut + 10.0
    intervals.append((start, duration))
    return intervals


def test_detect_speech_intervals_uses_pinned_silero_vad_parameters(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}
    decoded_audio = object()

    faster_whisper = ModuleType("faster_whisper")
    faster_whisper.__path__ = []  # type: ignore[attr-defined]

    def fake_decode_audio(path: str, sampling_rate: int):
        calls["decode"] = (path, sampling_rate)
        return decoded_audio

    faster_whisper.decode_audio = fake_decode_audio  # type: ignore[attr-defined]

    vad = ModuleType("faster_whisper.vad")

    class FakeVadOptions:
        def __init__(self, **kwargs) -> None:
            calls["vad_options"] = kwargs

    def fake_get_speech_timestamps(audio, options, sampling_rate=16000):
        calls["vad_call"] = (audio, options, sampling_rate)
        return [
            {"start": 0, "end": 16_000},
            {"start": 24_000, "end": 32_000},
        ]

    vad.VadOptions = FakeVadOptions  # type: ignore[attr-defined]
    vad.get_speech_timestamps = fake_get_speech_timestamps  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad)

    intervals = media.detect_speech_intervals(Path("audio.m4a"))

    assert intervals == [(0.0, 1.0), (1.5, 2.0)]
    assert calls["decode"] == ("audio.m4a", 16_000)
    assert calls["vad_options"] == {
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 0,
    }
    assert calls["vad_call"][0] is decoded_audio  # type: ignore[index]


@pytest.mark.parametrize("duration", [60.0, 300.0, 301.0, 900.0, 3600.0])
def test_planned_chunks_cover_audio_once_with_normal_duration_bounds(
    duration: float,
) -> None:
    plan = make_plan(duration)

    assert plan.schema_version == 4
    assert_valid_layout(plan, duration)
    assert all(
        chunk.end_boundary in {"silence", "hard", "audio_end"}
        for chunk in plan.chunks
    )


def test_short_audio_is_one_allowed_chunk_exception() -> None:
    plan = make_plan(59.5)

    assert plan.num_workers == 1
    assert plan.cpu_threads == 24
    assert len(plan.chunks) == 1
    assert plan.chunks[0].duration == 59.5
    assert_valid_layout(plan, 59.5)


def test_automatic_plan_uses_largest_feasible_budget_divisor() -> None:
    plan = make_plan(900.0, cpu_count=32)

    assert plan.cpu_budget == 24
    assert plan.num_workers == 12
    assert plan.cpu_threads == 2
    assert len(plan.chunks) == 12
    assert [chunk.duration for chunk in plan.chunks] == pytest.approx([75.0] * 12)
    assert_valid_layout(plan, 900.0)


def test_automatic_workers_only_consider_cpu_budget_divisors() -> None:
    plan = make_plan(900.0, cpu_count=31)

    assert plan.cpu_budget == 23
    assert plan.num_workers == 1
    assert plan.cpu_threads == 23


@pytest.mark.parametrize(
    ("num_workers", "cpu_threads", "expected_workers", "expected_threads"),
    [
        (5, 4, 5, 4),
        (7, None, 7, 3),
        (None, 5, 4, 5),
    ],
)
def test_explicit_worker_parameters_have_documented_precedence(
    num_workers: int | None,
    cpu_threads: int | None,
    expected_workers: int,
    expected_threads: int,
) -> None:
    plan = make_plan(
        900.0,
        num_workers=num_workers,
        cpu_threads=cpu_threads,
    )

    assert plan.num_workers == expected_workers
    assert plan.cpu_threads == expected_threads
    assert len(plan.chunks) % expected_workers == 0
    assert plan.num_workers * plan.cpu_threads <= plan.cpu_budget


@pytest.mark.parametrize(
    ("duration", "num_workers", "cpu_threads"),
    [
        (900.0, 5, 5),
        (900.0, 16, None),
        (59.5, 2, None),
        (900.0, None, 25),
        (900.0, 0, None),
        (900.0, None, 0),
    ],
)
def test_invalid_explicit_worker_configuration_is_rejected(
    duration: float,
    num_workers: int | None,
    cpu_threads: int | None,
) -> None:
    with pytest.raises(ValueError):
        make_plan(
            duration,
            num_workers=num_workers,
            cpu_threads=cpu_threads,
        )


def test_no_speech_and_one_long_speech_interval_use_balanced_hard_cuts() -> None:
    no_speech = make_plan(900.0, speech_intervals=[])
    continuous_speech = make_plan(900.0, speech_intervals=[(0.0, 900.0)])

    assert no_speech == continuous_speech
    assert [chunk.end_boundary for chunk in no_speech.chunks[:-1]] == ["hard"] * 11
    assert [chunk.duration for chunk in no_speech.chunks] == pytest.approx([75.0] * 12)


def test_planner_uses_silence_midpoint_before_adding_hard_cuts() -> None:
    plan = make_plan(
        240.0,
        num_workers=3,
        speech_intervals=[(0.0, 100.0), (140.0, 240.0)],
    )

    assert len(plan.chunks) == 3
    assert 120.0 in chunk_ends(plan)
    assert [chunk.end_boundary for chunk in plan.chunks].count("silence") == 1
    assert [chunk.end_boundary for chunk in plan.chunks].count("hard") == 1
    assert_valid_layout(plan, 240.0)


def test_hard_cut_count_has_priority_over_batch_count() -> None:
    cut_points = [100.0, 240.0, 400.0, 580.0, 780.0]
    plan = make_plan(
        900.0,
        num_workers=3,
        speech_intervals=speech_intervals_for_cut_points(900.0, cut_points),
    )

    assert len(plan.chunks) == 6
    assert chunk_ends(plan) == cut_points + [900.0]
    assert [chunk.end_boundary for chunk in plan.chunks] == ["silence"] * 5 + [
        "audio_end"
    ]


def test_batch_count_breaks_tie_after_hard_cut_count() -> None:
    cut_points = [150.0, 300.0, 450.0, 600.0, 750.0]
    plan = make_plan(
        900.0,
        num_workers=3,
        speech_intervals=speech_intervals_for_cut_points(900.0, cut_points),
    )

    assert len(plan.chunks) == 3
    assert chunk_ends(plan) == [300.0, 600.0, 900.0]
    assert [chunk.end_boundary for chunk in plan.chunks] == [
        "silence",
        "silence",
        "audio_end",
    ]


def test_squared_duration_error_breaks_equal_cost_natural_cut_tie() -> None:
    plan = make_plan(
        240.0,
        num_workers=2,
        speech_intervals=[(0.0, 90.0), (110.0, 115.0), (125.0, 240.0)],
    )

    assert len(plan.chunks) == 2
    assert chunk_ends(plan) == [120.0, 240.0]
    assert [chunk.duration for chunk in plan.chunks] == [120.0, 120.0]


def test_reversing_speech_interval_input_produces_the_same_plan() -> None:
    intervals = speech_intervals_for_cut_points(
        900.0,
        [100.0, 240.0, 400.0, 580.0, 780.0],
    )

    forward = make_plan(900.0, num_workers=3, speech_intervals=intervals)
    reverse = make_plan(900.0, num_workers=3, speech_intervals=list(reversed(intervals)))

    assert reverse == forward


def test_dense_natural_boundaries_scale_to_one_hour() -> None:
    duration = 3600.0
    speech_intervals = [(0.0, 2.5)]
    speech_intervals.extend(
        (float(start) + 0.5, float(start) + 2.5)
        for start in range(3, 3597, 3)
    )
    speech_intervals.append((3597.5, duration))

    started = perf_counter()
    plan = make_plan(
        duration,
        cpu_count=64,
        speech_intervals=speech_intervals,
    )
    elapsed = perf_counter() - started

    assert plan.num_workers == 48
    assert len(plan.chunks) == 48
    assert [chunk.duration for chunk in plan.chunks] == pytest.approx([75.0] * 48)
    assert [chunk.end_boundary for chunk in plan.chunks[:-1]] == ["silence"] * 47
    assert_valid_layout(plan, duration)
    assert elapsed < 5.0


def test_dense_boundaries_with_long_speech_gap_scale_and_use_one_hard_cut() -> None:
    duration = 3600.0
    speech_intervals = [(0.0, 2.5)]
    speech_intervals.extend(
        (float(start) + 0.5, float(start) + 2.5)
        for start in range(3, 1497, 3)
    )
    speech_intervals.append((1497.5, 1901.5))
    speech_intervals.extend(
        (float(start) + 0.5, float(start) + 2.5)
        for start in range(1902, 3597, 3)
    )
    speech_intervals.append((3597.5, duration))

    started = perf_counter()
    plan = make_plan(
        duration,
        cpu_count=64,
        speech_intervals=speech_intervals,
    )
    elapsed = perf_counter() - started

    assert plan.num_workers == 48
    assert len(plan.chunks) == 48
    assert [chunk.end_boundary for chunk in plan.chunks].count("hard") == 1
    assert_valid_layout(plan, duration)
    assert elapsed < 5.0


def test_plan_records_effective_vad_parameters() -> None:
    plan = make_plan(900.0)

    assert plan.vad_parameters == parallel_asr.VadParameters(
        threshold=0.5,
        min_speech_duration_ms=250,
        min_silence_duration_ms=500,
        speech_pad_ms=0,
        sampling_rate=16_000,
    )
    assert math.isfinite(plan.source_audio.duration)
