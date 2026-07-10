import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.asr import parallel as parallel_asr
from scripts.asr.parallel import media, runner, state, worker
from scripts.process_logging import LoggingSession
from scripts.runtime_options import TranscribeOptions
from scripts.utils import read_json, write_json


def make_source(duration: float = 900.0) -> parallel_asr.AsrSourceAudio:
    return parallel_asr.AsrSourceAudio(
        path="audio.m4a",
        size=123,
        mtime=456.0,
        duration=duration,
    )


def make_plan(
    duration: float = 900.0,
    cpu_count: int | None = 32,
    language: str = "zh",
) -> parallel_asr.ParallelAsrPlan:
    return parallel_asr.build_parallel_asr_plan(
        duration_seconds=duration,
        cpu_count=cpu_count,
        source_audio=make_source(duration),
        options=TranscribeOptions(model="model-dir", language=language),
    )


def empty_chunk_results(plan: parallel_asr.ParallelAsrPlan) -> dict[str, dict]:
    return {
        parallel_asr.chunk_key(chunk): {
            "macro_index": chunk.macro_index,
            "chunk_index": chunk.chunk_index,
            "elapsed_seconds": 0.0,
            "segments": [],
        }
        for chunk in plan.asr_chunks
    }


def make_chunk_result(
    plan: parallel_asr.ParallelAsrPlan,
    chunk: parallel_asr.AsrChunkPlan,
) -> dict:
    macro = plan.macro_chunks[chunk.macro_index]
    return {
        "schema_version": parallel_asr.SCHEMA_VERSION,
        "macro_index": chunk.macro_index,
        "chunk_index": chunk.chunk_index,
        "start": chunk.start,
        "duration": chunk.duration,
        "source_start": chunk.source_start,
        "source_duration": chunk.source_duration,
        "overlap": {"left": chunk.left_overlap, "right": chunk.right_overlap},
        "source": asdict(plan.source_audio),
        "plan": asdict(plan),
        "model": {
            "path": plan.model,
            "language": plan.language,
            "beam_size": plan.beam_size,
            "device": plan.device,
            "compute_type": plan.compute_type,
            "cpu_threads": macro.cpu_threads,
            "model_workers": macro.model_workers,
        },
        "elapsed_seconds": 1.0,
        "segments": [],
    }


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
    monkeypatch.setattr(media, "resolve_ffmpeg_location", lambda: "C:/ffmpeg/bin")

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(stdout='{"format": {"duration": "12.345"}}')

    monkeypatch.setattr(media.subprocess, "run", fake_run)

    assert parallel_asr.probe_audio_duration(Path("audio.m4a")) == 12.345
    assert commands[0][0].endswith("ffprobe.exe")
    assert commands[0][-1] == "audio.m4a"


def test_split_asr_chunks_uses_overlap_and_audio_format_args(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(900.0, 32)
    commands: list[list[str]] = []
    monkeypatch.setattr(media, "resolve_ffmpeg_location", lambda: "C:/ffmpeg/bin")
    monkeypatch.setattr(media, "_run_subprocess", lambda command: commands.append(command) or "")

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
    monkeypatch.setattr(state.os, "replace", lambda src, dst: calls.append((Path(src), Path(dst))))
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
    result = make_chunk_result(old_plan, chunk)
    write_json(
        workspace_tmp_path / "chunk_results" / "macro_000_chunk_000.json",
        result,
    )

    assert parallel_asr.load_valid_chunk_results(workspace_tmp_path, new_plan) == {}


@pytest.mark.parametrize(
    "new_plan",
    [
        replace(make_plan(), model="other-model"),
        replace(make_plan(), language="en"),
        replace(make_plan(), beam_size=3),
        replace(make_plan(), device="cuda"),
        replace(make_plan(), compute_type="float16"),
        replace(make_plan(), cpu_budget=25),
        make_plan(cpu_count=8),
    ],
)
def test_load_valid_chunk_results_rejects_changed_plan(
    workspace_tmp_path: Path,
    new_plan: parallel_asr.ParallelAsrPlan,
) -> None:
    old_plan = make_plan()
    chunk = old_plan.asr_chunks[0]
    write_json(
        workspace_tmp_path / "chunk_results" / "macro_000_chunk_000.json",
        make_chunk_result(old_plan, chunk),
    )

    assert parallel_asr.load_valid_chunk_results(workspace_tmp_path, new_plan) == {}


def test_parallel_runner_logs_plan_rebuild_and_macro_details(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(runner, "split_asr_chunks", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "transcribe_whisper_chunks",
        lambda plan, *_args: empty_chunk_results(plan),
    )

    with LoggingSession(workspace_tmp_path / "transcribe.log"):
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(model="model-dir", language="zh"),
            output_dir,
            900.0,
        )
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(model="model-dir", language="zh"),
            output_dir,
            900.0,
        )
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(model="model-dir", language="en"),
            output_dir,
            900.0,
        )

    terminal = capsys.readouterr().out
    assert (
        "[Transcribe] plan: status=created, macros=1, chunks=6, "
        "cpu_budget=24, overlap=5.000s\n"
    ) in terminal
    assert (
        "[Transcribe] macro_000 plan: chunks=6, task_workers=6, "
        "model_workers=6, cpu_threads=4\n"
    ) in terminal
    assert "[Transcribe] plan: status=reused" in terminal
    assert "[Transcribe] cached plan incompatible; rebuilding\n" in terminal
    assert "[Transcribe] plan: status=rebuilt" in terminal
    assert "[Transcribe] merge succeeded: chunks=6, segments=0\n" in terminal


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

    monkeypatch.setattr(worker, "_resolve_model_path", lambda _model: "model-dir")
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


def test_transcribe_whisper_chunks_logs_cached_results(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    plan = make_plan(120.0, 1)
    chunk = plan.asr_chunks[0]
    key = parallel_asr.chunk_key(chunk)
    write_json(
        workspace_tmp_path / "chunk_results" / f"{key}.json",
        make_chunk_result(plan, chunk),
    )
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][key]["status"] = "running"
    parallel_asr.write_progress(workspace_tmp_path / "progress.json", progress)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: pytest.fail("model loaded")),
    )

    with LoggingSession(workspace_tmp_path / "transcribe.log"):
        results = parallel_asr.transcribe_whisper_chunks(
            plan,
            TranscribeOptions(model="model-dir", language="zh"),
            workspace_tmp_path,
        )

    assert list(results) == [key]
    assert capsys.readouterr().out == (
        "[Transcribe] cache: reused=1, ignored=0, pending=0, total=1\n"
        f"[Transcribe] {key} reused cached result\n"
    )


@pytest.mark.parametrize(
    ("status", "retry_count", "resume_message"),
    [
        ("running", 0, "resumed after interruption"),
        ("pending", 1, "resumed for retry (1/1)"),
    ],
)
def test_transcribe_whisper_chunks_logs_resume_and_success(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
    status: str,
    retry_count: int,
    resume_message: str,
) -> None:
    plan = make_plan(120.0, 1)
    chunk = plan.asr_chunks[0]
    key = parallel_asr.chunk_key(chunk)
    chunk_path = workspace_tmp_path / chunk.path
    chunk_path.parent.mkdir(parents=True)
    chunk_path.write_bytes(b"wav")
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][key].update(
        {"status": status, "retry_count": retry_count}
    )
    parallel_asr.write_progress(workspace_tmp_path / "progress.json", progress)

    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, *_args, **_kwargs):
            return iter([]), SimpleNamespace(language="zh")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )

    with LoggingSession(workspace_tmp_path / "transcribe.log"):
        parallel_asr.transcribe_whisper_chunks(
            plan,
            TranscribeOptions(model="model-dir", language="zh"),
            workspace_tmp_path,
        )

    terminal = capsys.readouterr().out
    assert f"[Transcribe] {key} {resume_message}\n" in terminal
    assert f"[Transcribe] {key} succeeded\n" in terminal


def test_transcribe_whisper_chunks_logs_retry_and_traceback(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    plan = make_plan(120.0, 1)
    chunk = plan.asr_chunks[0]
    key = parallel_asr.chunk_key(chunk)
    chunk_path = workspace_tmp_path / chunk.path
    chunk_path.parent.mkdir(parents=True)
    chunk_path.write_bytes(b"wav")

    class FailingWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, *_args, **_kwargs):
            raise RuntimeError("chunk detail")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FailingWhisperModel),
    )
    log_path = workspace_tmp_path / "transcribe.log"

    with LoggingSession(log_path):
        with pytest.raises(RuntimeError, match="ASR chunk failed after retry"):
            parallel_asr.transcribe_whisper_chunks(
                plan,
                TranscribeOptions(model="model-dir", language="zh"),
                workspace_tmp_path,
            )

    terminal = capsys.readouterr().out
    assert f"[Transcribe] {key} failed; retrying (1/1): chunk detail\n" in terminal
    assert f"[Transcribe] {key} failed after 1 retry: chunk detail\n" in terminal
    assert "Traceback (most recent call last)" not in terminal
    log_text = log_path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in log_text
    assert "RuntimeError: chunk detail" in log_text


def test_transcribe_whisper_chunks_reports_exhausted_retry_on_resume(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    plan = make_plan(120.0, 1)
    chunk = plan.asr_chunks[0]
    key = parallel_asr.chunk_key(chunk)
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][key].update(
        {"status": "failed", "retry_count": 1, "error": "previous detail"}
    )
    parallel_asr.write_progress(workspace_tmp_path / "progress.json", progress)

    with LoggingSession(workspace_tmp_path / "transcribe.log"):
        with pytest.raises(RuntimeError, match="ASR chunk failed after retry"):
            parallel_asr.transcribe_whisper_chunks(
                plan,
                TranscribeOptions(model="model-dir", language="zh"),
                workspace_tmp_path,
            )

    terminal = capsys.readouterr().out
    assert f"[Transcribe] {key} failed after 1 retry: previous detail\n" in terminal
    assert "resumed for retry" not in terminal


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


def test_merge_chunk_results_deduplicates_cross_chunk_chinese_text() -> None:
    plan = make_plan()
    first, second = plan.asr_chunks[:2]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 149.0,
            "text": "前文这是一个足够长的重复片段",
        }
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {
            "id": 1,
            "start": 6.0,
            "end": 14.0,
            "text": "这是一个足够长的重复片段后续",
        }
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert [segment["text"] for segment in merged] == [
        "前文这是一个足够长的重复片段",
        "后续",
    ]


def test_merge_chunk_results_only_deduplicates_across_chunks() -> None:
    plan = make_plan()
    first = plan.asr_chunks[0]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 10.0,
            "end": 20.0,
            "text": "前文这是一个足够长的重复片段",
        },
        {
            "id": 1,
            "start": 21.0,
            "end": 30.0,
            "text": "这是一个足够长的重复片段后续",
        },
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert [segment["text"] for segment in merged] == [
        "前文这是一个足够长的重复片段",
        "这是一个足够长的重复片段后续",
    ]


def test_merge_chunk_results_deduplicates_chinese_with_punctuation_and_spaces() -> None:
    plan = make_plan()
    first, second = plan.asr_chunks[:2]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 149.0,
            "text": "前文我们主张资本要向善，如果你这个互联网资本",
        }
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {
            "id": 1,
            "start": 6.0,
            "end": 14.0,
            "text": "我们 主张资本要向善如果你这个互联网资本，开始整天琢磨着",
        }
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert [segment["text"] for segment in merged] == [
        "前文我们主张资本要向善，如果你这个互联网资本",
        "开始整天琢磨着",
    ]


def test_merge_chunk_results_deduplicates_english_on_word_boundaries() -> None:
    plan = make_plan(language="en")
    first, second = plan.asr_chunks[:2]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 149.0,
            "text": "The system keeps the whole overlapping phrase",
        }
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {
            "id": 1,
            "start": 6.0,
            "end": 14.0,
            "text": "keeps, the whole OVERLAPPING phrase intact after merge",
        }
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert [segment["text"] for segment in merged] == [
        "The system keeps the whole overlapping phrase",
        "intact after merge",
    ]


def test_merge_chunk_results_keeps_english_suffix_when_match_ends_mid_word() -> None:
    plan = make_plan(language="en")
    first, second = plan.asr_chunks[:2]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 149.0,
            "text": "The system keeps the whole overlapping phrase",
        }
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {
            "id": 1,
            "start": 6.0,
            "end": 14.0,
            "text": "keeps the whole overlapping phraseology matters",
        }
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert [segment["text"] for segment in merged] == [
        "The system keeps the whole overlapping phrase",
        "keeps the whole overlapping phraseology matters",
    ]


def test_merge_chunk_results_deduplicates_reported_cross_chunk_regression() -> None:
    plan = make_plan()
    first, second = plan.asr_chunks[:2]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 149.0,
            "text": "但我们的加速作业是什么加速啊?中左翼的加速作业,是吧?我们主张资本要向善,如果你这个互联网资本,开始整天琢磨着,如何从老百姓彭彭彭,彭彭。",
        }
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {
            "id": 1,
            "start": 6.0,
            "end": 14.0,
            "text": "我们主张资本要向善,如果你这个互联网资本开始整天琢磨着如何从老百姓彭彭关关的强点最后的一个钢柴,那我就要稍微敲打敲打你。",
        }
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert merged[1]["text"] == "强点最后的一个钢柴,那我就要稍微敲打敲打你。"


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
