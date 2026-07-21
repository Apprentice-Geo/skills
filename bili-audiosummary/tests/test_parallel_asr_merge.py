import json
from types import SimpleNamespace

import pytest

from scripts.asr.chunking import SAMPLE_RATE
from scripts.asr.parallel.merge import merge_chunk_results
from scripts.asr.parallel.metrics import write_metrics
from scripts.asr.parallel.plan import AsrChunkPlan


def make_chunk(
    index: int,
    start: float,
    duration: float = 10.0,
    end_boundary: str = "silence",
) -> AsrChunkPlan:
    return AsrChunkPlan(
        index=index,
        start_sample=round(start * SAMPLE_RATE),
        end_sample=round((start + duration) * SAMPLE_RATE),
        end_boundary=end_boundary,
        estimated_speech_samples=round(duration / 2 * SAMPLE_RATE),
    )


def make_plan(
    *chunks: AsrChunkPlan,
    language: str = "zh",
    num_workers: int = 1,
    cpu_threads: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        language=language,
        chunks=list(chunks),
        num_workers=num_workers,
        cpu_threads=cpu_threads,
    )


def chunk_key(index: int) -> str:
    return f"chunk_{index:03d}"


def chunk_result(
    index: int,
    segments: list[dict],
    elapsed_seconds: float = 1.0,
) -> dict:
    return {
        "chunk_index": index,
        "elapsed_seconds": elapsed_seconds,
        "segments": segments,
    }


def test_merge_offsets_from_chunk_start_and_sorts_results_stably() -> None:
    first = make_chunk(0, 100.0)
    second = make_chunk(1, 200.0, end_boundary="audio_end")
    plan = make_plan(first, second)
    results = {
        chunk_key(1): chunk_result(
            1,
            [{"id": 30, "start": 1.0, "end": 2.0, "text": "第三段"}],
        ),
        chunk_key(0): chunk_result(
            0,
            [
                {"id": 20, "start": 4.0, "end": 5.0, "text": "第二段"},
                {"id": 10, "start": 1.0, "end": 2.0, "text": "第一段"},
            ],
        ),
    }

    merged = merge_chunk_results(plan, results)

    assert merged == [
        {"id": 0, "start": 101.0, "end": 102.0, "text": "第一段"},
        {"id": 1, "start": 104.0, "end": 105.0, "text": "第二段"},
        {"id": 2, "start": 201.0, "end": 202.0, "text": "第三段"},
    ]


@pytest.mark.parametrize(
    ("language", "left_text", "right_text", "expected_text"),
    [
        ("zh", "第一段", "第二段", "第一段第二段"),
        ("en", " first segment ", " second segment ", "first segment second segment"),
    ],
)
def test_merge_combines_truly_overlapping_segments_by_language(
    language: str,
    left_text: str,
    right_text: str,
    expected_text: str,
) -> None:
    first = make_chunk(0, 0.0)
    second = make_chunk(1, 10.0, end_boundary="audio_end")
    plan = make_plan(first, second, language=language)
    results = {
        chunk_key(0): chunk_result(
            0,
            [{"id": 0, "start": 8.0, "end": 12.0, "text": left_text}],
        ),
        chunk_key(1): chunk_result(
            1,
            [{"id": 1, "start": 0.0, "end": 4.0, "text": right_text}],
        ),
    }

    assert merge_chunk_results(plan, results) == [
        {"id": 0, "start": 8.0, "end": 14.0, "text": expected_text}
    ]


def test_merge_does_not_combine_segments_that_only_touch_at_endpoint() -> None:
    first = make_chunk(0, 0.0)
    second = make_chunk(1, 10.0, end_boundary="audio_end")
    plan = make_plan(first, second)
    results = {
        chunk_key(0): chunk_result(
            0,
            [{"id": 7, "start": 8.0, "end": 10.0, "text": "前段"}],
        ),
        chunk_key(1): chunk_result(
            1,
            [{"id": 8, "start": 0.0, "end": 2.0, "text": "后段"}],
        ),
    }

    assert merge_chunk_results(plan, results) == [
        {"id": 0, "start": 8.0, "end": 10.0, "text": "前段"},
        {"id": 1, "start": 10.0, "end": 12.0, "text": "后段"},
    ]


def test_merge_keeps_repeated_text_when_time_ranges_overlap() -> None:
    first = make_chunk(0, 0.0)
    second = make_chunk(1, 10.0, end_boundary="audio_end")
    plan = make_plan(first, second)
    repeated = "重复片段"
    results = {
        chunk_key(0): chunk_result(
            0,
            [{"id": 0, "start": 8.0, "end": 12.0, "text": repeated}],
        ),
        chunk_key(1): chunk_result(
            1,
            [{"id": 1, "start": 0.0, "end": 4.0, "text": repeated}],
        ),
    }

    assert merge_chunk_results(plan, results)[0]["text"] == repeated + repeated


def test_merge_combines_chained_overlaps_within_one_chunk() -> None:
    chunk = make_chunk(0, 100.0, end_boundary="audio_end")
    plan = make_plan(chunk)
    results = {
        chunk_key(0): chunk_result(
            0,
            [
                {"id": 1, "start": 0.0, "end": 4.0, "text": "甲"},
                {"id": 2, "start": 3.0, "end": 7.0, "text": "乙"},
                {"id": 3, "start": 6.0, "end": 8.0, "text": "丙"},
            ],
        )
    }

    assert merge_chunk_results(plan, results) == [
        {"id": 0, "start": 100.0, "end": 108.0, "text": "甲乙丙"}
    ]


def test_merge_rejects_segment_with_end_before_start() -> None:
    chunk = make_chunk(0, 0.0, end_boundary="audio_end")
    plan = make_plan(chunk)
    results = {
        chunk_key(0): chunk_result(
            0,
            [{"id": 0, "start": 5.0, "end": 4.0, "text": "非法"}],
        )
    }

    with pytest.raises(RuntimeError, match="end is earlier than start"):
        merge_chunk_results(plan, results)


def test_merge_rejects_missing_chunk_result() -> None:
    first = make_chunk(0, 0.0)
    second = make_chunk(1, 10.0, end_boundary="audio_end")
    plan = make_plan(first, second)
    results = {chunk_key(0): chunk_result(0, [])}

    with pytest.raises(RuntimeError, match="Missing ASR chunk result: chunk_001"):
        merge_chunk_results(plan, results)


def test_metrics_records_flat_worker_chunk_and_final_segment_fields(
    workspace_tmp_path,
) -> None:
    chunks = [
        make_chunk(
            index,
            index * 60.0,
            end_boundary="audio_end" if index == 3 else "silence",
        )
        for index in range(4)
    ]
    plan = make_plan(*chunks, num_workers=2, cpu_threads=3)
    results = {
        chunk_key(index): chunk_result(
            index,
            [{"id": item} for item in range(index + 1)],
            elapsed_seconds=index + 0.25,
        )
        for index in reversed(range(4))
    }
    path = workspace_tmp_path / "metrics.json"

    metrics = write_metrics(
        path,
        plan,
        total_elapsed_seconds=9.876,
        chunk_results=results,
        failed_chunks=[],
        segment_count=2,
    )

    assert metrics["num_workers"] == 2
    assert metrics["cpu_threads"] == 3
    assert metrics["chunk_count"] == 4
    assert metrics["batch_count"] == 2
    assert metrics["hard_cut_count"] == 0
    assert metrics["chunk_estimated_speech_durations"] == [5.0] * 4
    assert metrics["max_estimated_speech_duration"] == 5.0
    assert metrics["speech_load_msre"] == 0.0
    assert metrics["chunk_elapsed_seconds"] == [
        {"chunk_index": 0, "elapsed_seconds": 0.25},
        {"chunk_index": 1, "elapsed_seconds": 1.25},
        {"chunk_index": 2, "elapsed_seconds": 2.25},
        {"chunk_index": 3, "elapsed_seconds": 3.25},
    ]
    assert metrics["segment_count"] == 2
    assert all("macro" not in key for key in metrics)
    assert all("macro_index" not in item for item in metrics["chunk_elapsed_seconds"])
    assert json.loads(path.read_text(encoding="utf-8")) == metrics
