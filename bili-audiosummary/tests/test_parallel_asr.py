import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import parallel_asr
from runtime_options import TranscribeOptions
from utils import read_json, write_json


def make_source(duration: float = 900.0) -> parallel_asr.AsrSourceAudio:
    return parallel_asr.AsrSourceAudio(
        path="audio.m4a",
        size=123,
        mtime=456.0,
        duration=duration,
    )


def make_plan(duration: float = 900.0, cpu_count: int | None = 32) -> parallel_asr.ParallelAsrPlan:
    return parallel_asr.build_parallel_asr_plan(
        duration_seconds=duration,
        cpu_count=cpu_count,
        source_audio=make_source(duration),
        options=TranscribeOptions(model="model-dir", language="zh"),
    )


@pytest.mark.parametrize("duration", [120.0, 900.0, 1440.0, 1800.0, 3600.0])
@pytest.mark.parametrize("cpu_count", [8, 12, 16, 24, 32, None])
def test_parallel_asr_plan_respects_worker_and_chunk_invariants(
    duration: float,
    cpu_count: int | None,
) -> None:
    plan = make_plan(duration, cpu_count)

    for macro in plan.macro_chunks:
        assert macro.task_workers == macro.model_workers
        assert len(macro.chunks) >= macro.task_workers
        assert len(macro.chunks) % macro.task_workers == 0
        assert all(chunk.duration >= 120.0 for chunk in macro.chunks)


@pytest.mark.parametrize(
    ("cpu_count", "expected"),
    [
        (8, (6, 1)),
        (12, (6, 1)),
        (16, (6, 2)),
        (24, (6, 3)),
        (32, (6, 4)),
    ],
)
def test_parallel_asr_plan_uses_six_150_second_chunks_for_15_minutes(
    cpu_count: int,
    expected: tuple[int, int],
) -> None:
    plan = make_plan(900.0, cpu_count)

    assert plan.task_workers == expected[0]
    assert plan.model_workers == expected[0]
    assert plan.cpu_threads == expected[1]
    assert len(plan.asr_chunks) == 6
    assert [chunk.duration for chunk in plan.asr_chunks] == [150.0] * 6


def test_parallel_asr_plan_uses_macro_chunks_for_audio_longer_than_24_minutes() -> None:
    plan = make_plan(3600.0, 32)

    assert [(macro.start, macro.duration) for macro in plan.macro_chunks] == [
        (0.0, 1440.0),
        (1440.0, 1440.0),
        (2880.0, 720.0),
    ]
    assert [macro.task_workers for macro in plan.macro_chunks] == [8, 8, 6]


def test_probe_audio_duration_uses_packaged_ffprobe(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(parallel_asr, "resolve_ffmpeg_location", lambda: "C:/ffmpeg/bin")

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(stdout='{"format": {"duration": "12.345"}}')

    monkeypatch.setattr(parallel_asr.subprocess, "run", fake_run)

    assert parallel_asr.probe_audio_duration(Path("audio.m4a")) == 12.345
    assert commands[0][0].endswith("ffprobe.exe")
    assert commands[0][-1] == "audio.m4a"


def test_split_asr_chunks_uses_overlap_and_audio_format_args(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(900.0, 32)
    commands: list[list[str]] = []
    monkeypatch.setattr(parallel_asr, "resolve_ffmpeg_location", lambda: "C:/ffmpeg/bin")
    monkeypatch.setattr(parallel_asr, "_run_subprocess", lambda command: commands.append(command) or "")

    parallel_asr.split_asr_chunks(Path("audio.m4a"), plan, workspace_tmp_path)

    first = commands[0]
    second = commands[1]
    assert first[0].endswith("ffmpeg.exe")
    assert first[first.index("-ss") + 1] == "0.000"
    assert first[first.index("-t") + 1] == "155.000"
    assert first[first.index("-ar") + 1] == "16000"
    assert first[first.index("-ac") + 1] == "1"
    assert first[-1].endswith("chunks/macro_000/chunk_000.wav")
    assert second[second.index("-ss") + 1] == "145.000"
    assert second[second.index("-t") + 1] == "160.000"


def test_progress_resume_resets_running_and_retryable_failed() -> None:
    plan = make_plan()
    first, second, third = plan.asr_chunks[:3]
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][parallel_asr.chunk_key(first)]["status"] = "running"
    progress["chunks"][parallel_asr.chunk_key(second)].update(
        {"status": "failed", "retry_count": 0}
    )
    progress["chunks"][parallel_asr.chunk_key(third)].update(
        {"status": "failed", "retry_count": 1}
    )

    resumed = parallel_asr.prepare_progress_for_resume(plan, progress, set())

    assert resumed["chunks"][parallel_asr.chunk_key(first)]["status"] == "pending"
    assert resumed["chunks"][parallel_asr.chunk_key(second)]["status"] == "pending"
    assert resumed["chunks"][parallel_asr.chunk_key(third)]["status"] == "failed"
    assert parallel_asr.failed_chunks_blocking_merge(resumed) == [
        parallel_asr.chunk_key(third)
    ]


def test_progress_resume_reuses_valid_result_as_succeeded() -> None:
    plan = make_plan()
    first = plan.asr_chunks[0]
    key = parallel_asr.chunk_key(first)
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][key]["status"] = "running"

    resumed = parallel_asr.prepare_progress_for_resume(plan, progress, {key})

    assert resumed["chunks"][key]["status"] == "succeeded"


def test_chunk_result_atomic_write_uses_tmp_then_json(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(parallel_asr.os, "replace", lambda src, dst: calls.append((Path(src), Path(dst))))
    target = workspace_tmp_path / "chunk_results" / "macro_000_chunk_000.json"

    parallel_asr.write_chunk_result_atomic(target, {"ok": True})

    assert calls == [(target.with_suffix(".json.tmp"), target)]
    assert target.with_suffix(".json.tmp").exists()


def test_load_valid_chunk_results_rejects_changed_source(
    workspace_tmp_path: Path,
) -> None:
    old_plan = make_plan()
    new_plan = parallel_asr.build_parallel_asr_plan(
        duration_seconds=900.0,
        cpu_count=32,
        source_audio=parallel_asr.AsrSourceAudio(
            path="audio.m4a",
            size=999,
            mtime=456.0,
            duration=900.0,
        ),
        options=TranscribeOptions(model="model-dir", language="zh"),
    )
    chunk = old_plan.asr_chunks[0]
    result = {
        "schema_version": parallel_asr.SCHEMA_VERSION,
        "macro_index": chunk.macro_index,
        "chunk_index": chunk.chunk_index,
        "start": chunk.start,
        "duration": chunk.duration,
        "overlap": {"left": chunk.left_overlap, "right": chunk.right_overlap},
        "source": asdict(old_plan.source_audio),
        "model": {"path": "model-dir"},
        "elapsed_seconds": 1.0,
        "segments": [],
    }
    write_json(
        workspace_tmp_path / "chunk_results" / "macro_000_chunk_000.json",
        result,
    )

    assert parallel_asr.load_valid_chunk_results(workspace_tmp_path, new_plan) == {}


def test_transcribe_whisper_chunks_uses_one_model_and_writes_chunk_results(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan()
    for chunk in plan.asr_chunks:
        chunk_path = workspace_tmp_path / chunk.path
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        chunk_path.write_bytes(b"wav")

    instances: list[dict] = []

    class FakeWhisperModel:
        def __init__(self, model_path, **kwargs) -> None:
            instances.append({"model_path": model_path, **kwargs})

        def transcribe(self, *_args, **_kwargs):
            segment = SimpleNamespace(id=7, start=0.0, end=1.0, text="测试")
            info = SimpleNamespace(language="zh", language_probability=1.0)
            return iter([segment]), info

    monkeypatch.setattr(parallel_asr, "_resolve_model_path", lambda _model: "model-dir")
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )

    results = parallel_asr.transcribe_whisper_chunks(
        plan,
        TranscribeOptions(model="model-dir", language="zh"),
        workspace_tmp_path,
    )

    assert len(instances) == 1
    assert instances[0]["num_workers"] == plan.model_workers
    assert instances[0]["cpu_threads"] == plan.cpu_threads
    assert len(results) == len(plan.asr_chunks)
    assert (workspace_tmp_path / "chunk_results" / "macro_000_chunk_000.json").exists()


def test_merge_chunk_results_drops_overlap_segments_and_reassigns_ids() -> None:
    plan = make_plan()
    first, second = plan.asr_chunks[:2]
    results = {
        parallel_asr.chunk_key(first): {
            "macro_index": 0,
            "chunk_index": 0,
            "segments": [
                {"id": 10, "start": 1.0, "end": 2.0, "text": "keep first"},
                {"id": 11, "start": 153.0, "end": 154.0, "text": "drop right overlap"},
            ],
        },
        parallel_asr.chunk_key(second): {
            "macro_index": 0,
            "chunk_index": 1,
            "segments": [
                {"id": 12, "start": 1.0, "end": 2.0, "text": "drop left overlap"},
                {"id": 13, "start": 6.0, "end": 7.0, "text": "keep second"},
            ],
        },
    }
    for chunk in plan.asr_chunks[2:]:
        results[parallel_asr.chunk_key(chunk)] = {
            "macro_index": chunk.macro_index,
            "chunk_index": chunk.chunk_index,
            "segments": [],
        }

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert [segment["text"] for segment in merged] == ["keep first", "keep second"]
    assert [segment["id"] for segment in merged] == [0, 1]
    assert merged[1]["start"] == 151.0


def test_merge_chunk_results_rejects_segment_with_end_before_start() -> None:
    plan = make_plan()
    chunk = plan.asr_chunks[0]
    results = {
        parallel_asr.chunk_key(item): {
            "macro_index": item.macro_index,
            "chunk_index": item.chunk_index,
            "segments": [],
        }
        for item in plan.asr_chunks
    }
    results[parallel_asr.chunk_key(chunk)]["segments"] = [
        {"id": 0, "start": 10.0, "end": 9.0, "text": "bad"}
    ]

    with pytest.raises(RuntimeError, match="end is earlier"):
        parallel_asr.merge_chunk_results(plan, results)


def test_write_metrics_records_worker_chunk_segment_and_failure_fields(
    workspace_tmp_path: Path,
) -> None:
    plan = make_plan()
    chunk = plan.asr_chunks[0]
    result = {
        "macro_index": chunk.macro_index,
        "chunk_index": chunk.chunk_index,
        "elapsed_seconds": 1.23,
        "segments": [{"id": 0}],
    }
    path = workspace_tmp_path / "metrics.json"

    metrics = parallel_asr.write_metrics(
        path,
        plan,
        9.87,
        {parallel_asr.chunk_key(chunk): result},
        ["macro_000_chunk_002"],
        [{"macro_index": 0, "elapsed_seconds": 1.23, "chunk_count": 6}],
    )

    assert metrics["task_workers"] == plan.task_workers
    assert metrics["model_workers"] == plan.model_workers
    assert metrics["cpu_threads"] == plan.cpu_threads
    assert metrics["chunk_count"] == len(plan.asr_chunks)
    assert metrics["segment_count"] == 1
    assert metrics["failed_chunks"] == ["macro_000_chunk_002"]
    assert metrics["macro_elapsed_seconds"] == [
        {"macro_index": 0, "elapsed_seconds": 1.23, "chunk_count": 6}
    ]
    assert read_json(path) == metrics


def test_build_macro_elapsed_from_results_uses_max_chunk_elapsed() -> None:
    plan = make_plan()
    results = {}
    for index, chunk in enumerate(plan.asr_chunks):
        results[parallel_asr.chunk_key(chunk)] = {
            "macro_index": chunk.macro_index,
            "chunk_index": chunk.chunk_index,
            "elapsed_seconds": float(index + 1),
            "segments": [],
        }

    assert parallel_asr.build_macro_elapsed_from_results(plan, results) == [
        {"macro_index": 0, "elapsed_seconds": 6.0, "chunk_count": 6}
    ]
