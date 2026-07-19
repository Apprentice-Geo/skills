import json
import sys
from dataclasses import asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.asr import parallel as parallel_asr
from scripts.asr.parallel import media, runner, worker
from scripts.process_logging import LoggingSession
from scripts.runtime_options import TranscribeOptions
from scripts.utils import read_json, write_json


def make_source(duration: float = 120.0) -> parallel_asr.AsrSourceAudio:
    return parallel_asr.AsrSourceAudio(
        path="audio.m4a",
        size=123,
        mtime=456.0,
        duration=duration,
    )


def make_vad_parameters():
    return parallel_asr.VadParameters(
        threshold=0.35,
        neg_threshold=0.25,
        min_speech_duration_ms=0,
        min_silence_duration_ms=300,
        max_speech_duration_s=None,
        speech_pad_ms=0,
        sampling_rate=16000,
    )


def make_plan(
    *,
    source_audio: parallel_asr.AsrSourceAudio | None = None,
    chunk_count: int = 2,
    num_workers: int = 1,
    cpu_threads: int = 1,
):
    duration = 120.0
    chunk_duration = duration / chunk_count
    chunks = [
        parallel_asr.AsrChunkPlan(
            index=index,
            start=index * chunk_duration,
            duration=chunk_duration,
            path=f"chunks/chunk_{index:03d}.wav",
            end_boundary="audio_end" if index == chunk_count - 1 else "silence",
            estimated_speech_duration=chunk_duration / 2,
        )
        for index in range(chunk_count)
    ]
    return parallel_asr.ParallelAsrPlan(
        schema_version=parallel_asr.SCHEMA_VERSION,
        source_audio=source_audio or make_source(duration),
        provider="whisper",
        model="model-dir",
        language="zh",
        beam_size=5,
        device="cpu",
        compute_type="float32",
        vad_parameters=make_vad_parameters(),
        planning_parameters=parallel_asr.PlanningParameters(),
        cpu_budget=3,
        num_workers=num_workers,
        cpu_threads=cpu_threads,
        chunks=chunks,
    )


def make_chunk_result(plan, chunk, *, schema_version: int | None = None) -> dict:
    return {
        "schema_version": plan.schema_version if schema_version is None else schema_version,
        "chunk_index": chunk.index,
        "start": chunk.start,
        "duration": chunk.duration,
        "end_boundary": chunk.end_boundary,
        "source": asdict(plan.source_audio),
        "plan": asdict(plan),
        "chunk_audio_path": chunk.path,
        "model": {
            "path": plan.model,
            "language": plan.language,
            "beam_size": plan.beam_size,
            "device": plan.device,
            "compute_type": plan.compute_type,
            "cpu_threads": plan.cpu_threads,
            "num_workers": plan.num_workers,
        },
        "elapsed_seconds": 1.0,
        "info": {},
        "segments": [],
    }


def install_fake_whisper(monkeypatch, model_class) -> None:
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=model_class),
    )


def isolate_runner_outputs(monkeypatch) -> None:
    monkeypatch.setattr(runner, "split_asr_chunks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "merge_chunk_results", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "write_metrics", lambda *_args, **_kwargs: None)
    if hasattr(runner, "build_macro_elapsed_from_results"):
        monkeypatch.setattr(
            runner,
            "build_macro_elapsed_from_results",
            lambda *_args, **_kwargs: [],
        )


def test_schema_five_uses_flat_global_chunks_with_speech_estimates() -> None:
    assert parallel_asr.SCHEMA_VERSION == 5
    assert [field.name for field in fields(parallel_asr.AsrChunkPlan)] == [
        "index",
        "start",
        "duration",
        "path",
        "end_boundary",
        "estimated_speech_duration",
    ]
    plan_fields = {field.name for field in fields(parallel_asr.ParallelAsrPlan)}
    assert {
        "chunks",
        "num_workers",
        "cpu_threads",
        "vad_parameters",
        "planning_parameters",
    } <= plan_fields
    assert not {"macro_chunks", "asr_chunks", "overlap_seconds"} & plan_fields


def test_schema_four_plan_and_result_are_not_reused(workspace_tmp_path: Path) -> None:
    legacy_plan = {
        "schema_version": 4,
        "source_audio": asdict(make_source()),
        "provider": "whisper",
        "model": "model-dir",
        "language": "zh",
        "beam_size": 5,
        "device": "cpu",
        "compute_type": "float32",
        "cpu_budget": 3,
        "task_workers": 1,
        "model_workers": 1,
        "cpu_threads": 1,
        "macro_chunks": [],
        "asr_chunks": [],
        "overlap_seconds": 5.0,
    }
    legacy_plan_path = workspace_tmp_path / "legacy_plan.json"
    write_json(legacy_plan_path, legacy_plan)

    with pytest.raises(ValueError, match="schema_version"):
        parallel_asr.load_plan(legacy_plan_path)

    plan = make_plan()
    old_chunk, current_chunk = plan.chunks
    parallel_asr.write_chunk_result_atomic(
        parallel_asr.chunk_result_path(workspace_tmp_path, old_chunk),
        make_chunk_result(plan, old_chunk, schema_version=4),
    )
    parallel_asr.write_chunk_result_atomic(
        parallel_asr.chunk_result_path(workspace_tmp_path, current_chunk),
        make_chunk_result(plan, current_chunk),
    )

    assert parallel_asr.chunk_result_path(
        workspace_tmp_path, old_chunk
    ) == workspace_tmp_path / "chunk_results" / "chunk_000.json"
    assert set(parallel_asr.load_valid_chunk_results(workspace_tmp_path, plan)) == {
        "chunk_001"
    }


def test_exact_cache_match_skips_vad_and_model_on_second_run(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    vad_calls: list[Path] = []
    model_instances: list[dict] = []

    def fake_detect(path, _vad_parameters):
        vad_calls.append(Path(path))
        return [(1.25, 8.5)]

    class FakeWhisperModel:
        def __init__(self, model_path, **kwargs) -> None:
            model_instances.append({"model_path": model_path, **kwargs})

        def transcribe(self, _audio_path, **_kwargs):
            return iter([]), SimpleNamespace(language="zh")

    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        runner,
        "detect_speech_intervals",
        fake_detect,
        raising=False,
    )
    isolate_runner_outputs(monkeypatch)
    install_fake_whisper(monkeypatch, FakeWhisperModel)
    options = TranscribeOptions(
        model="model-dir",
        language="zh",
        num_workers=1,
        cpu_threads=1,
    )

    with LoggingSession(workspace_tmp_path / "parallel-cache.log"):
        runner.run_parallel_whisper_transcribe(audio_path, options, output_dir, 120.0)
        runner.run_parallel_whisper_transcribe(audio_path, options, output_dir, 120.0)

    assert vad_calls == [audio_path]
    assert len(model_instances) == 1
    assert json.loads(
        (output_dir / "asr_parallel" / "vad_result.json").read_text(
            encoding="utf-8"
        )
    ) == {
        "schema_version": 1,
        "source": {
            "path": audio_path.as_posix(),
            "size": 5,
            "mtime": audio_path.stat().st_mtime,
            "duration": 120.0,
        },
            "parameters": {
                "threshold": 0.35,
                "neg_threshold": 0.25,
                "min_speech_duration_ms": 0,
                "min_silence_duration_ms": 300,
                "max_speech_duration_s": None,
                "speech_pad_ms": 0,
                "sampling_rate": 16000,
        },
        "speech_intervals": [{"start": 1.25, "end": 8.5}],
    }
    assert "[Transcribe] VAD: skipped; reused matching ASR plan" in capsys.readouterr().out


def test_valid_vad_result_is_reused_when_plan_rebuilds(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    vad_calls: list[Path] = []

    def fake_detect(path, _vad_parameters):
        vad_calls.append(Path(path))
        return []

    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, _audio_path, **_kwargs):
            return iter([]), SimpleNamespace(language="zh")

    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(runner, "detect_speech_intervals", fake_detect)
    isolate_runner_outputs(monkeypatch)
    install_fake_whisper(monkeypatch, FakeWhisperModel)

    with LoggingSession(workspace_tmp_path / "vad-cache.log"):
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(
                model="model-dir",
                language="zh",
                beam_size=5,
                num_workers=1,
                cpu_threads=1,
            ),
            output_dir,
            120.0,
        )
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(
                model="model-dir",
                language="zh",
                beam_size=3,
                num_workers=1,
                cpu_threads=1,
            ),
            output_dir,
            120.0,
        )

    assert vad_calls == [audio_path]
    assert "[Transcribe] VAD cache: reused vad_result.json" in capsys.readouterr().out


def test_empty_vad_result_is_a_valid_cache_entry(workspace_tmp_path: Path) -> None:
    source = make_source()
    parameters = make_vad_parameters()
    path = workspace_tmp_path / "vad_result.json"

    parallel_asr.write_vad_result(path, source, parameters, [])

    assert parallel_asr.load_valid_vad_result(path, source, parameters) == []


@pytest.mark.parametrize(
    "mismatch",
    ["schema", "source", "parameters", "negative", "overlap", "out_of_bounds"],
)
def test_vad_cache_rejects_invalid_identity_or_intervals(
    workspace_tmp_path: Path,
    mismatch: str,
) -> None:
    source = make_source()
    parameters = make_vad_parameters()
    path = workspace_tmp_path / "vad_result.json"
    parallel_asr.write_vad_result(
        path,
        source,
        parameters,
        [(0.0, 10.0), (20.0, 30.0)],
    )
    data = read_json(path)
    if mismatch == "schema":
        data["schema_version"] = 0
    elif mismatch == "source":
        data["source"]["size"] += 1
    elif mismatch == "parameters":
        data["parameters"]["threshold"] = 0.4
    elif mismatch == "negative":
        data["speech_intervals"][0]["start"] = -1.0
    elif mismatch == "overlap":
        data["speech_intervals"][1]["start"] = 9.0
    else:
        data["speech_intervals"][1]["end"] = source.duration + 1.0
    write_json(path, data)

    assert parallel_asr.load_valid_vad_result(path, source, parameters) is None


def test_vad_cache_rejects_corrupt_json(workspace_tmp_path: Path) -> None:
    source = make_source()
    parameters = make_vad_parameters()
    path = workspace_tmp_path / "vad_result.json"
    path.write_text("{", encoding="utf-8")

    assert parallel_asr.load_valid_vad_result(path, source, parameters) is None


@pytest.mark.parametrize("mismatch", ["source", "asr", "vad", "planning", "worker"])
def test_cache_identity_change_rebuilds_plan(
    workspace_tmp_path: Path,
    monkeypatch,
    mismatch: str,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    workspace_dir = output_dir / "asr_parallel"
    source = parallel_asr.source_audio_fingerprint(audio_path, 120.0)
    expected_plan = make_plan(source_audio=source)
    if mismatch == "source":
        cached_plan = replace(
            expected_plan,
            source_audio=replace(source, size=source.size + 1),
        )
    elif mismatch == "asr":
        cached_plan = replace(expected_plan, beam_size=3)
    elif mismatch == "vad":
        cached_plan = replace(
            expected_plan,
            vad_parameters=replace(expected_plan.vad_parameters, threshold=0.4),
        )
    elif mismatch == "planning":
        cached_plan = replace(
            expected_plan,
            planning_parameters=parallel_asr.PlanningParameters(
                min_chunk_seconds=30.0,
                max_chunk_seconds=300.0,
            ),
        )
    else:
        cached_plan = replace(expected_plan, num_workers=2)
    parallel_asr.write_plan(workspace_dir / "asr_plan.json", cached_plan)
    parallel_asr.write_progress(
        workspace_dir / "progress.json",
        parallel_asr.initial_progress(cached_plan),
    )
    vad_calls: list[Path] = []
    build_calls: list[dict] = []

    def fake_detect(path, _vad_parameters):
        vad_calls.append(Path(path))
        return []

    def fake_build(**kwargs):
        build_calls.append(kwargs)
        return expected_plan

    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        runner,
        "detect_speech_intervals",
        fake_detect,
        raising=False,
    )
    monkeypatch.setattr(runner, "build_parallel_asr_plan", fake_build)
    monkeypatch.setattr(
        runner,
        "transcribe_whisper_chunks",
        lambda *_args, **_kwargs: {},
    )
    isolate_runner_outputs(monkeypatch)

    runner.run_parallel_whisper_transcribe(
        audio_path,
        TranscribeOptions(
            model="model-dir",
            language="zh",
            num_workers=1,
            cpu_threads=1,
        ),
        output_dir,
        120.0,
    )

    assert vad_calls == [audio_path]
    assert len(build_calls) == 1
    assert parallel_asr.load_plan(workspace_dir / "asr_plan.json") == expected_plan


def test_invalid_worker_configuration_fails_before_model_vad_or_workspace(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    output_dir = workspace_tmp_path / "output"
    monkeypatch.setattr(
        runner,
        "_resolve_model_path",
        lambda *_args: pytest.fail("model path resolved before worker validation"),
    )
    monkeypatch.setattr(
        runner,
        "detect_speech_intervals",
        lambda *_args: pytest.fail("VAD ran before worker validation"),
    )

    with pytest.raises(ValueError, match="No legal chunk count"):
        runner.run_parallel_whisper_transcribe(
            audio_path,
            TranscribeOptions(
                model="model-dir",
                num_workers=2,
            ),
            output_dir,
            59.5,
        )

    assert not (output_dir / "asr_parallel").exists()


def test_split_asr_chunks_uses_flat_start_and_duration(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan()
    commands: list[list[str]] = []
    monkeypatch.setattr(media, "resolve_ffmpeg_location", lambda: "C:/ffmpeg/bin")
    monkeypatch.setattr(
        media,
        "_run_subprocess",
        lambda command: commands.append(command) or "",
    )

    parallel_asr.split_asr_chunks(Path("audio.m4a"), plan, workspace_tmp_path)

    assert len(commands) == 2
    for index, command in enumerate(commands):
        assert command[command.index("-ss") + 1] == f"{index * 60:.3f}"
        assert command[command.index("-t") + 1] == "60.000"
        assert command[command.index("-ac") + 1] == "1"
        assert command[command.index("-ar") + 1] == "16000"
        assert command[-1].endswith(f"chunks/chunk_{index:03d}.wav")


def test_valid_result_wins_over_failed_progress(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(chunk_count=1)
    chunk = plan.chunks[0]
    key = parallel_asr.chunk_key(chunk)
    parallel_asr.write_chunk_result_atomic(
        parallel_asr.chunk_result_path(workspace_tmp_path, chunk),
        make_chunk_result(plan, chunk),
    )
    progress = parallel_asr.initial_progress(plan)
    progress["chunks"][key].update(
        {"status": "failed", "retry_count": 1, "error": "stale failure"}
    )
    parallel_asr.write_progress(workspace_tmp_path / "progress.json", progress)
    install_fake_whisper(
        monkeypatch,
        lambda *_args, **_kwargs: pytest.fail("model loaded for valid cached result"),
    )

    results = parallel_asr.transcribe_whisper_chunks(
        plan,
        TranscribeOptions(model="model-dir", language="zh"),
        workspace_tmp_path,
    )

    assert set(results) == {key}
    resumed = parallel_asr.load_progress(workspace_tmp_path / "progress.json")
    assert resumed["chunks"][key]["status"] == "succeeded"
    assert resumed["chunks"][key]["retry_count"] == 0


@pytest.mark.parametrize("mismatch", ["source", "asr", "vad", "planning", "worker"])
def test_chunk_result_is_ignored_when_cache_identity_changes(
    workspace_tmp_path: Path,
    mismatch: str,
) -> None:
    old_plan = make_plan(chunk_count=1)
    chunk = old_plan.chunks[0]
    parallel_asr.write_chunk_result_atomic(
        parallel_asr.chunk_result_path(workspace_tmp_path, chunk),
        make_chunk_result(old_plan, chunk),
    )
    if mismatch == "source":
        current_plan = replace(
            old_plan,
            source_audio=replace(old_plan.source_audio, size=999),
        )
    elif mismatch == "asr":
        current_plan = replace(old_plan, beam_size=3)
    elif mismatch == "vad":
        current_plan = replace(
            old_plan,
            vad_parameters=replace(old_plan.vad_parameters, threshold=0.4),
        )
    elif mismatch == "planning":
        current_plan = replace(
            old_plan,
            planning_parameters=parallel_asr.PlanningParameters(
                min_chunk_seconds=30.0,
                max_chunk_seconds=300.0,
            ),
        )
    else:
        current_plan = replace(old_plan, cpu_threads=2)

    assert parallel_asr.load_valid_chunk_results(
        workspace_tmp_path,
        current_plan,
    ) == {}


def test_plan_progress_and_chunk_result_writes_use_atomic_replace(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    from scripts.asr.parallel import state

    real_replace = state.os.replace
    replacements: list[tuple[Path, Path]] = []

    def replace_spy(source, target) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(state.os, "replace", replace_spy)
    plan = make_plan(chunk_count=1)
    targets = [
        workspace_tmp_path / "asr_plan.json",
        workspace_tmp_path / "progress.json",
        workspace_tmp_path / "chunk_results" / "chunk_000.json",
    ]

    parallel_asr.write_plan(targets[0], plan)
    parallel_asr.write_progress(targets[1], parallel_asr.initial_progress(plan))
    parallel_asr.write_chunk_result_atomic(
        targets[2],
        make_chunk_result(plan, plan.chunks[0]),
    )

    assert [target for _source, target in replacements] == targets
    assert all(source.parent == target.parent for source, target in replacements)
    assert all(source.suffix == ".tmp" for source, _target in replacements)
    assert all(target.exists() for target in targets)


def test_worker_uses_one_model_and_never_passes_initial_prompt(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(num_workers=2)
    for chunk in plan.chunks:
        path = workspace_tmp_path / chunk.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"wav")
    instances: list[dict] = []
    transcribe_calls: list[tuple[str, dict]] = []

    class FakeWhisperModel:
        def __init__(self, model_path, **kwargs) -> None:
            instances.append({"model_path": model_path, **kwargs})

        def transcribe(self, audio_path, **kwargs):
            transcribe_calls.append((str(audio_path), kwargs))
            return iter([]), SimpleNamespace(language="zh")

    install_fake_whisper(monkeypatch, FakeWhisperModel)

    results = parallel_asr.transcribe_whisper_chunks(
        plan,
        TranscribeOptions(model="model-dir", language="zh"),
        workspace_tmp_path,
    )

    assert len(instances) == 1
    assert instances[0]["num_workers"] == 2
    assert instances[0]["cpu_threads"] == 1
    assert len(transcribe_calls) == 2
    assert all(call[1]["vad_filter"] is True for call in transcribe_calls)
    assert all("vad_parameters" not in call[1] for call in transcribe_calls)
    assert all("initial_prompt" not in call[1] for call in transcribe_calls)
    assert set(results) == {"chunk_000", "chunk_001"}
    assert sorted(path.name for path in (workspace_tmp_path / "chunk_results").glob("*.json")) == [
        "chunk_000.json",
        "chunk_001.json",
    ]


def test_failed_chunk_gets_one_retry_per_run_and_a_new_budget_next_run(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(chunk_count=1)
    chunk_path = workspace_tmp_path / plan.chunks[0].path
    chunk_path.parent.mkdir(parents=True)
    chunk_path.write_bytes(b"wav")
    attempts = {"count": 0}
    recover = {"value": False}

    class RecoveringWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, *_args, **_kwargs):
            attempts["count"] += 1
            if not recover["value"]:
                raise RuntimeError("chunk failed")
            return iter([]), SimpleNamespace(language="zh")

    install_fake_whisper(monkeypatch, RecoveringWhisperModel)

    with pytest.raises(RuntimeError, match="failed after retry"):
        parallel_asr.transcribe_whisper_chunks(
            plan,
            TranscribeOptions(model="model-dir", language="zh"),
            workspace_tmp_path,
        )
    assert attempts["count"] == 2

    recover["value"] = True
    results = parallel_asr.transcribe_whisper_chunks(
        plan,
        TranscribeOptions(model="model-dir", language="zh"),
        workspace_tmp_path,
    )

    assert attempts["count"] == 3
    assert set(results) == {"chunk_000"}
    progress = parallel_asr.load_progress(workspace_tmp_path / "progress.json")
    assert progress["chunks"]["chunk_000"]["retry_count"] == 0
    assert progress["chunks"]["chunk_000"]["status"] == "succeeded"
