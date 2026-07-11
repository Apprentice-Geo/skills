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
    *,
    num_workers: int | None = None,
    cpu_threads: int | None = None,
) -> parallel_asr.ParallelAsrPlan:
    return parallel_asr.build_parallel_asr_plan(
        duration_seconds=duration,
        cpu_count=cpu_count,
        source_audio=make_source(duration),
        options=TranscribeOptions(
            model="model-dir",
            language=language,
            num_workers=num_workers,
            cpu_threads=cpu_threads,
        ),
    )


def replace_plan_chunk(
    plan: parallel_asr.ParallelAsrPlan,
    replacement: parallel_asr.AsrChunkPlan,
) -> parallel_asr.ParallelAsrPlan:
    def replace_matching(chunks: list[parallel_asr.AsrChunkPlan]):
        return [
            replacement
            if (
                chunk.macro_index,
                chunk.chunk_index,
            )
            == (replacement.macro_index, replacement.chunk_index)
            else chunk
            for chunk in chunks
        ]

    return replace(
        plan,
        macro_chunks=[
            replace(macro, chunks=replace_matching(macro.chunks))
            for macro in plan.macro_chunks
        ],
        asr_chunks=replace_matching(plan.asr_chunks),
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
    assert [
        (
            macro.task_workers,
            macro.model_workers,
            macro.cpu_threads,
            len(macro.chunks),
        )
        for macro in plan.macro_chunks
    ] == [(8, 8, 3, 8), (8, 8, 3, 8), (6, 6, 4, 6)]


def test_parallel_asr_plan_uses_schema_three_global_chunk_coordinates() -> None:
    plan = make_plan(3600.0, 32)

    assert parallel_asr.SCHEMA_VERSION == 3
    assert plan.schema_version == 3
    assert all(
        macro.start <= chunk.start < macro.start + macro.duration
        for macro in plan.macro_chunks
        for chunk in macro.chunks
    )
    first_boundary_left = plan.macro_chunks[0].chunks[-1]
    first_boundary_right = plan.macro_chunks[1].chunks[0]
    second_boundary_left = plan.macro_chunks[1].chunks[-1]
    second_boundary_right = plan.macro_chunks[2].chunks[0]
    assert (
        max(first_boundary_left.source_start, first_boundary_right.source_start),
        min(
            first_boundary_left.source_start + first_boundary_left.source_duration,
            first_boundary_right.source_start + first_boundary_right.source_duration,
        ),
    ) == (1435.0, 1445.0)
    assert (
        max(second_boundary_left.source_start, second_boundary_right.source_start),
        min(
            second_boundary_left.source_start + second_boundary_left.source_duration,
            second_boundary_right.source_start + second_boundary_right.source_duration,
        ),
    ) == (2875.0, 2885.0)
    assert plan.asr_chunks[0].source_start == 0.0
    last = plan.asr_chunks[-1]
    assert last.source_start + last.source_duration == 3600.0


@pytest.mark.parametrize(
    ("num_workers", "cpu_threads", "expected_workers", "expected_threads"),
    [
        (1, None, 1, 24),
        (3, None, 3, 8),
        (None, 4, 6, 4),
        (None, 5, 4, 5),
        (3, 2, 3, 2),
    ],
)
def test_parallel_asr_plan_applies_explicit_worker_overrides(
    num_workers: int | None,
    cpu_threads: int | None,
    expected_workers: int,
    expected_threads: int,
) -> None:
    plan = make_plan(
        900.0,
        32,
        num_workers=num_workers,
        cpu_threads=cpu_threads,
    )

    assert plan.task_workers == expected_workers
    assert plan.model_workers == expected_workers
    assert plan.cpu_threads == expected_threads
    assert all(
        (macro.task_workers, macro.model_workers, macro.cpu_threads)
        == (expected_workers, expected_workers, expected_threads)
        for macro in plan.macro_chunks
    )


def test_parallel_asr_plan_applies_explicit_values_to_every_macro() -> None:
    plan = make_plan(3600.0, 32, num_workers=3, cpu_threads=2)

    assert [
        (macro.task_workers, macro.model_workers, macro.cpu_threads)
        for macro in plan.macro_chunks
    ] == [(3, 3, 2), (3, 3, 2), (3, 3, 2)]


@pytest.mark.parametrize(
    ("duration", "num_workers", "cpu_threads", "expected_fragments"),
    [
        (900.0, 0, None, ("num_workers=0", "macro_index=0", "1..8")),
        (900.0, -1, None, ("num_workers=-1", "macro_index=0", "1..8")),
        (900.0, 9, None, ("num_workers=9", "macro_index=0", "1..8")),
        (900.0, None, 0, ("cpu_threads=0", "macro_index=0", ">= 1")),
        (900.0, None, -1, ("cpu_threads=-1", "macro_index=0", ">= 1")),
        (
            900.0,
            6,
            5,
            ("num_workers=6", "cpu_threads=5", "macro_index=0", "cpu_budget=24"),
        ),
        (
            900.0,
            None,
            25,
            ("cpu_threads=25", "macro_index=0", "cpu_budget=24"),
        ),
        (
            3600.0,
            8,
            None,
            ("num_workers=8", "macro_index=2", "supported_chunk_count=6"),
        ),
    ],
)
def test_parallel_asr_plan_rejects_invalid_explicit_configuration(
    duration: float,
    num_workers: int | None,
    cpu_threads: int | None,
    expected_fragments: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError) as exc_info:
        make_plan(
            duration,
            32,
            num_workers=num_workers,
            cpu_threads=cpu_threads,
        )

    message = str(exc_info.value)
    assert all(fragment in message for fragment in expected_fragments)


def test_invalid_explicit_configuration_fails_before_workspace_or_model(
    workspace_tmp_path: Path,
    monkeypatch,
    mocker,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    split_mock = mocker.patch("scripts.asr.parallel.runner.split_asr_chunks")
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 32)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(
            WhisperModel=lambda *_args, **_kwargs: pytest.fail("model loaded")
        ),
    )

    with pytest.raises(ValueError, match="macro_index=2"):
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(
                model="model-dir",
                language="zh",
                num_workers=8,
            ),
            output_dir,
            3600.0,
        )

    split_mock.assert_not_called()
    assert not (output_dir / "asr_parallel").exists()


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


def test_split_asr_chunks_uses_global_source_start_across_macro_boundary(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(3600.0, 32)
    commands: list[list[str]] = []
    monkeypatch.setattr(media, "resolve_ffmpeg_location", lambda: "C:/ffmpeg/bin")
    monkeypatch.setattr(
        media,
        "_run_subprocess",
        lambda command: commands.append(command) or "",
    )

    parallel_asr.split_asr_chunks(Path("audio.m4a"), plan, workspace_tmp_path)

    command = next(
        item
        for item in commands
        if item[-1].endswith("chunks/macro_001/chunk_000.wav")
    )
    assert command[command.index("-ss") + 1] == "1435.000"


def test_schema_two_plan_and_chunk_result_are_not_reused(
    workspace_tmp_path: Path,
) -> None:
    current_plan = make_plan()
    old_plan = replace(current_plan, schema_version=2)
    plan_path = workspace_tmp_path / "asr_plan.json"
    parallel_asr.write_plan(plan_path, old_plan)
    loaded, status = runner._load_or_create_plan(plan_path, current_plan)

    assert status == "rebuilt"
    assert loaded == current_plan

    chunk = current_plan.asr_chunks[0]
    old_result = make_chunk_result(current_plan, chunk)
    old_result["schema_version"] = 2
    write_json(
        workspace_tmp_path / "chunk_results" / f"{parallel_asr.chunk_key(chunk)}.json",
        old_result,
    )

    assert parallel_asr.load_valid_chunk_results(workspace_tmp_path, current_plan) == {}


def test_progress_resume_resets_every_chunk_without_a_valid_result() -> None:
    plan = make_plan()
    pending, running, retryable, exhausted, missing_result = plan.asr_chunks[:5]
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][parallel_asr.chunk_key(pending)].update(
        {"status": "pending", "retry_count": 1, "error": "old pending"}
    )
    progress["chunks"][parallel_asr.chunk_key(running)].update(
        {"status": "running", "retry_count": 1, "error": "old running"}
    )
    progress["chunks"][parallel_asr.chunk_key(retryable)].update(
        {"status": "failed", "retry_count": 0, "error": "old failure"}
    )
    progress["chunks"][parallel_asr.chunk_key(exhausted)].update(
        {"status": "failed", "retry_count": 1, "error": "old exhausted"}
    )
    progress["chunks"][parallel_asr.chunk_key(missing_result)].update(
        {"status": "succeeded", "retry_count": 1, "error": "stale success"}
    )

    resumed = parallel_asr.prepare_progress_for_resume(plan, progress, set())

    for chunk in plan.asr_chunks:
        assert resumed["chunks"][parallel_asr.chunk_key(chunk)] == {
            "status": "pending",
            "retry_count": 0,
            "error": None,
            "result_path": f"chunk_results/{parallel_asr.chunk_key(chunk)}.json",
        }
    assert parallel_asr.failed_chunks_blocking_merge(resumed) == []


def test_progress_resume_reuses_valid_result_as_succeeded() -> None:
    plan = make_plan()
    first = plan.asr_chunks[0]
    key = parallel_asr.chunk_key(first)
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][key].update(
        {"status": "failed", "retry_count": 1, "error": "old failure"}
    )

    resumed = parallel_asr.prepare_progress_for_resume(plan, progress, {key})

    assert resumed["chunks"][key]["status"] == "succeeded"
    assert resumed["chunks"][key]["retry_count"] == 0
    assert resumed["chunks"][key]["error"] is None


@pytest.mark.parametrize("kind", ["plan", "progress", "chunk_result"])
def test_state_json_writes_use_same_directory_atomic_replace(
    workspace_tmp_path: Path,
    monkeypatch,
    kind: str,
) -> None:
    calls: list[tuple[Path, Path]] = []
    real_replace = state.os.replace

    def tracking_replace(src, dst) -> None:
        source = Path(src)
        target = Path(dst)
        calls.append((source, target))
        assert source.parent == target.parent
        assert source.exists()
        real_replace(source, target)

    monkeypatch.setattr(state.os, "replace", tracking_replace)
    plan = make_plan()
    if kind == "plan":
        target = workspace_tmp_path / "asr_plan.json"
        parallel_asr.write_plan(target, plan)
        expected = asdict(plan)
    elif kind == "progress":
        target = workspace_tmp_path / "progress.json"
        expected = parallel_asr.initial_progress(plan)
        parallel_asr.write_progress(target, expected)
    else:
        target = workspace_tmp_path / "chunk_results" / "macro_000_chunk_000.json"
        expected = {"ok": True}
        parallel_asr.write_chunk_result_atomic(target, expected)

    assert calls == [(target.with_suffix(target.suffix + ".tmp"), target)]
    assert read_json(target) == expected
    assert not target.with_suffix(target.suffix + ".tmp").exists()


@pytest.mark.parametrize("invalid_progress", ["truncated", "invalid_status"])
def test_transcribe_whisper_chunks_rebuilds_invalid_progress_and_reuses_results(
    workspace_tmp_path: Path,
    monkeypatch,
    invalid_progress: str,
) -> None:
    plan = make_plan(240.0, 1)
    first, second = plan.asr_chunks
    first_key = parallel_asr.chunk_key(first)
    second_key = parallel_asr.chunk_key(second)
    write_json(
        workspace_tmp_path / "chunk_results" / f"{first_key}.json",
        make_chunk_result(plan, first),
    )
    progress_path = workspace_tmp_path / "progress.json"
    if invalid_progress == "truncated":
        progress_path.write_text('{"schema_version":', encoding="utf-8")
    else:
        write_json(
            progress_path,
            {
                "schema_version": parallel_asr.SCHEMA_VERSION,
                "chunks": {
                    first_key: {"status": "not-a-status"},
                },
            },
        )

    transcribed_paths: list[str] = []

    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, audio_path, **_kwargs):
            transcribed_paths.append(str(audio_path))
            return iter([]), SimpleNamespace(language="zh")

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

    assert set(results) == {first_key, second_key}
    assert len(transcribed_paths) == 1
    assert transcribed_paths[0].endswith(second.path)
    progress = parallel_asr.load_progress(progress_path)
    assert progress["chunks"][first_key]["status"] == "succeeded"
    assert progress["chunks"][second_key]["status"] == "succeeded"


def test_transcribe_whisper_chunks_uses_all_cached_results_when_progress_is_corrupt(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(240.0, 1)
    for chunk in plan.asr_chunks:
        key = parallel_asr.chunk_key(chunk)
        write_json(
            workspace_tmp_path / "chunk_results" / f"{key}.json",
            make_chunk_result(plan, chunk),
        )
    (workspace_tmp_path / "progress.json").write_text("{", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(
            WhisperModel=lambda *_args, **_kwargs: pytest.fail("model loaded")
        ),
    )

    results = parallel_asr.transcribe_whisper_chunks(
        plan,
        TranscribeOptions(model="model-dir", language="zh"),
        workspace_tmp_path,
    )

    assert len(results) == len(plan.asr_chunks)
    assert all(
        item["status"] == "succeeded"
        for item in parallel_asr.load_progress(
            workspace_tmp_path / "progress.json"
        )["chunks"].values()
    )


@pytest.mark.parametrize("invalid_plan", ["{", "[]"])
def test_parallel_runner_rebuilds_invalid_plan_and_progress(
    workspace_tmp_path: Path,
    monkeypatch,
    invalid_plan: str,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    workspace_dir = output_dir / "asr_parallel"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "asr_plan.json").write_text(invalid_plan, encoding="utf-8")
    write_json(
        workspace_dir / "progress.json",
        {"schema_version": parallel_asr.SCHEMA_VERSION, "chunks": {}},
    )
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(runner, "split_asr_chunks", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "transcribe_whisper_chunks",
        lambda plan, *_args: empty_chunk_results(plan),
    )

    runner.run_parallel_whisper_transcribe(
        audio_path,
        TranscribeOptions(model="model-dir", language="zh"),
        output_dir,
        900.0,
    )

    rebuilt_plan = parallel_asr.load_plan(workspace_dir / "asr_plan.json")
    expected_plan = parallel_asr.build_parallel_asr_plan(
        duration_seconds=900.0,
        cpu_count=32,
        source_audio=parallel_asr.source_audio_fingerprint(audio_path, 900.0),
        options=TranscribeOptions(model="model-dir", language="zh"),
    )
    assert rebuilt_plan == expected_plan
    progress = parallel_asr.load_progress(workspace_dir / "progress.json")
    assert set(progress["chunks"]) == {
        parallel_asr.chunk_key(chunk) for chunk in rebuilt_plan.asr_chunks
    }
    assert all(item["status"] == "pending" for item in progress["chunks"].values())


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


def test_explicit_configuration_reaches_plan_model_results_and_log(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    model_configurations: list[tuple[int, int]] = []

    class FakeWhisperModel:
        def __init__(self, _model_path, **kwargs) -> None:
            model_configurations.append(
                (kwargs["num_workers"], kwargs["cpu_threads"])
            )

        def transcribe(self, _audio_path, **_kwargs):
            return iter([]), SimpleNamespace(language="zh")

    monkeypatch.setattr(runner.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(runner, "split_asr_chunks", lambda *_args: None)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )

    with LoggingSession(workspace_tmp_path / "transcribe.log"):
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(
                model="model-dir",
                language="zh",
                num_workers=3,
                cpu_threads=2,
            ),
            output_dir,
            900.0,
        )

    workspace_dir = output_dir / "asr_parallel"
    plan = parallel_asr.load_plan(workspace_dir / "asr_plan.json")
    assert [
        (macro.task_workers, macro.model_workers, macro.cpu_threads)
        for macro in plan.macro_chunks
    ] == [(3, 3, 2)]
    assert model_configurations == [(3, 2)]
    chunk_results = [
        read_json(path)
        for path in (workspace_dir / "chunk_results").glob("*.json")
    ]
    assert len(chunk_results) == len(plan.asr_chunks)
    assert all(
        (result["model"]["model_workers"], result["model"]["cpu_threads"])
        == (3, 2)
        for result in chunk_results
    )
    assert (
        "[Transcribe] macro_000 plan: chunks=6, task_workers=3, "
        "model_workers=3, cpu_threads=2\n"
    ) in capsys.readouterr().out


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


def test_transcribe_whisper_chunks_uses_each_macros_resolved_model_configuration(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(3600.0, 32)
    instances: list[tuple[int, int]] = []
    actual_by_path: dict[str, tuple[int, int]] = {}

    class FakeWhisperModel:
        def __init__(self, _model_path, **kwargs) -> None:
            self.configuration = (kwargs["num_workers"], kwargs["cpu_threads"])
            instances.append(self.configuration)

        def transcribe(self, audio_path, **_kwargs):
            actual_by_path[str(audio_path)] = self.configuration
            return iter([]), SimpleNamespace(language="zh")

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

    assert instances == [(8, 3), (6, 4)]
    assert len(results) == len(plan.asr_chunks)
    for result in results.values():
        recorded = (
            result["model"]["model_workers"],
            result["model"]["cpu_threads"],
        )
        assert recorded == actual_by_path[result["chunk_audio_path"]]


def test_transcribe_whisper_chunks_only_loads_configuration_for_pending_macro(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(3600.0, 32)
    last_macro = plan.macro_chunks[-1]
    for macro in plan.macro_chunks[:-1]:
        for chunk in macro.chunks:
            key = parallel_asr.chunk_key(chunk)
            write_json(
                workspace_tmp_path / "chunk_results" / f"{key}.json",
                make_chunk_result(plan, chunk),
            )
    instances: list[tuple[int, int]] = []

    class FakeWhisperModel:
        def __init__(self, _model_path, **kwargs) -> None:
            instances.append((kwargs["num_workers"], kwargs["cpu_threads"]))

        def transcribe(self, _audio_path, **_kwargs):
            return iter([]), SimpleNamespace(language="zh")

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

    assert instances == [(last_macro.model_workers, last_macro.cpu_threads)]
    assert len(results) == len(plan.asr_chunks)


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
        ("pending", 1, "resumed with a new retry budget (1/1)"),
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


def test_transcribe_whisper_chunks_gives_failed_chunk_a_new_budget_on_next_run(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(240.0, 1)
    first, target = plan.asr_chunks
    first_key = parallel_asr.chunk_key(first)
    target_key = parallel_asr.chunk_key(target)
    for chunk in plan.asr_chunks:
        chunk_path = workspace_tmp_path / chunk.path
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        chunk_path.write_bytes(b"wav")

    fail_target = {"value": True}
    attempts = {first_key: 0, target_key: 0}

    class RecoveringWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, audio_path, **_kwargs):
            key = first_key if str(audio_path).endswith(first.path) else target_key
            attempts[key] += 1
            if key == target_key and fail_target["value"]:
                raise RuntimeError("target failed")
            return iter([]), SimpleNamespace(language="zh")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=RecoveringWhisperModel),
    )

    with pytest.raises(RuntimeError, match="ASR chunk failed after retry"):
        parallel_asr.transcribe_whisper_chunks(
            plan,
            TranscribeOptions(model="model-dir", language="zh"),
            workspace_tmp_path,
        )

    failed_progress = parallel_asr.load_progress(
        workspace_tmp_path / "progress.json"
    )
    assert failed_progress["chunks"][first_key]["status"] == "succeeded"
    assert failed_progress["chunks"][target_key]["status"] == "failed"
    assert failed_progress["chunks"][target_key]["retry_count"] == 1
    assert attempts == {first_key: 1, target_key: 2}

    fail_target["value"] = False
    results = parallel_asr.transcribe_whisper_chunks(
        plan,
        TranscribeOptions(model="model-dir", language="zh"),
        workspace_tmp_path,
    )

    assert set(results) == {first_key, target_key}
    assert attempts == {first_key: 1, target_key: 3}
    resumed_progress = parallel_asr.load_progress(
        workspace_tmp_path / "progress.json"
    )
    assert resumed_progress["chunks"][target_key] == {
        "status": "succeeded",
        "retry_count": 0,
        "error": None,
        "result_path": f"chunk_results/{target_key}.json",
    }


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


def test_merge_chunk_results_offsets_multi_macro_segments_from_global_source_start() -> None:
    plan = make_plan(3600.0, 32)
    results = empty_chunk_results(plan)
    chunk = plan.macro_chunks[1].chunks[0]
    results[parallel_asr.chunk_key(chunk)]["segments"] = [
        {"id": 7, "start": 10.0, "end": 20.0, "text": "global time"}
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert merged == [
        {"id": 0, "start": 1445.0, "end": 1455.0, "text": "global time"}
    ]


def test_merge_chunk_results_keeps_similar_chinese_text_without_time_overlap() -> None:
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
        "这是一个足够长的重复片段后续",
    ]


@pytest.mark.parametrize(
    ("language", "previous_text", "current_text", "expected_current"),
    [
        (
            "zh",
            "前文这是一个足够长的重复片段",
            "这是一个足够长的重复片段后续",
            "后续",
        ),
        (
            "en",
            "The system keeps the whole overlapping phrase",
            "keeps, the whole OVERLAPPING phrase intact after merge",
            "intact after merge",
        ),
    ],
)
def test_merge_chunk_results_deduplicates_adjacent_boundary_with_time_evidence(
    language: str,
    previous_text: str,
    current_text: str,
    expected_current: str,
) -> None:
    plan = make_plan(language=language)
    first, second = plan.asr_chunks[:2]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {"id": 0, "start": 140.0, "end": 152.0, "text": previous_text}
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {"id": 1, "start": 4.0, "end": 12.0, "text": current_text}
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert merged == [
        {
            "id": 0,
            "start": 140.0,
            "end": second.source_start + 12.0,
            "text": previous_text + expected_current,
        }
    ]


def test_merge_chunk_results_treats_segment_endpoint_contact_as_no_overlap() -> None:
    plan = make_plan()
    first, second = plan.asr_chunks[:2]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 150.0,
            "text": "前文这是一个足够长的重复片段",
        }
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {
            "id": 1,
            "start": 5.0,
            "end": 15.0,
            "text": "这是一个足够长的重复片段后续",
        }
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert [segment["text"] for segment in merged] == [
        "前文这是一个足够长的重复片段",
        "这是一个足够长的重复片段后续",
    ]


def test_merge_chunk_results_rejects_overlapping_nonadjacent_chunks() -> None:
    plan = make_plan()
    first, _second, third = plan.asr_chunks[:3]
    abnormal_third = replace(
        third,
        start=150.0,
        source_start=145.0,
        source_duration=160.0,
        left_overlap=5.0,
        right_overlap=5.0,
    )
    plan = replace_plan_chunk(plan, abnormal_third)
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 152.0,
            "text": "前文这是一个足够长的重复片段",
        }
    ]
    results[parallel_asr.chunk_key(abnormal_third)]["segments"] = [
        {
            "id": 1,
            "start": 4.0,
            "end": 12.0,
            "text": "这是一个足够长的重复片段后续",
        }
    ]

    with pytest.raises(RuntimeError, match="not monotonically increasing"):
        parallel_asr.merge_chunk_results(plan, results)


def test_merge_chunk_results_rejects_overlap_without_shared_chunk_source_audio() -> None:
    plan = make_plan()
    first, second = plan.asr_chunks[:2]
    no_overlap_first = replace(first, source_duration=145.0, right_overlap=0.0)
    plan = replace_plan_chunk(plan, no_overlap_first)
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(no_overlap_first)]["segments"] = [
        {
            "id": 0,
            "start": 140.0,
            "end": 152.0,
            "text": "前文这是一个足够长的重复片段",
        }
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {
            "id": 1,
            "start": 4.0,
            "end": 12.0,
            "text": "这是一个足够长的重复片段后续",
        }
    ]

    with pytest.raises(RuntimeError, match="not monotonically increasing"):
        parallel_asr.merge_chunk_results(plan, results)


def test_merge_chunk_results_rejects_remaining_overlap_after_boundary_merge() -> None:
    plan = make_plan()
    first, second = plan.asr_chunks[:2]
    repeated = "这是一个足够长的重复片段"
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(first)]["segments"] = [
        {"id": 0, "start": 140.0, "end": 152.0, "text": repeated}
    ]
    results[parallel_asr.chunk_key(second)]["segments"] = [
        {"id": 1, "start": 4.0, "end": 12.0, "text": repeated},
        {
            "id": 2,
            "start": 5.0,
            "end": 13.0,
            "text": f"{repeated}后续",
        },
    ]

    with pytest.raises(RuntimeError, match="not monotonically increasing"):
        parallel_asr.merge_chunk_results(plan, results)


def test_merge_chunk_results_deduplicates_across_macro_boundary() -> None:
    plan = make_plan(3600.0, 32)
    left = plan.macro_chunks[0].chunks[-1]
    right = plan.macro_chunks[1].chunks[0]
    results = empty_chunk_results(plan)
    results[parallel_asr.chunk_key(left)]["segments"] = [
        {
            "id": 0,
            "start": 183.0,
            "end": 187.0,
            "text": "前文这是一个足够长的重复片段",
        }
    ]
    results[parallel_asr.chunk_key(right)]["segments"] = [
        {
            "id": 1,
            "start": 4.0,
            "end": 9.0,
            "text": "这是一个足够长的重复片段后续",
        }
    ]

    merged = parallel_asr.merge_chunk_results(plan, results)

    assert merged == [
        {
            "id": 0,
            "start": left.source_start + 183.0,
            "end": right.source_start + 9.0,
            "text": "前文这是一个足够长的重复片段后续",
        }
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


def test_merge_chunk_results_keeps_punctuated_chinese_without_time_overlap() -> None:
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
        "我们 主张资本要向善如果你这个互联网资本，开始整天琢磨着",
    ]


def test_merge_chunk_results_keeps_similar_english_without_time_overlap() -> None:
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
        "keeps, the whole OVERLAPPING phrase intact after merge",
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


def test_merge_chunk_results_keeps_reported_text_without_time_overlap() -> None:
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

    assert merged[1]["text"] == (
        "我们主张资本要向善,如果你这个互联网资本开始整天琢磨着"
        "如何从老百姓彭彭关关的强点最后的一个钢柴,那我就要稍微敲打敲打你。"
    )


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
