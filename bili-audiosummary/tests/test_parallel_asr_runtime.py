from __future__ import annotations

import sys
import types
from dataclasses import asdict, fields
from pathlib import Path

import numpy as np
import pytest

from scripts.asr import parallel as parallel_asr
from scripts.asr.chunking import SAMPLE_RATE, NormalizedAudio
from scripts.asr.parallel import runner
from scripts.asr.parallel.state import (
    initial_progress,
    load_valid_chunk_results,
    prepare_progress_for_resume,
)
from scripts.runtime_options import TranscribeOptions
from scripts.utils import read_json, write_json_atomic


def fake_audio(seconds: int) -> NormalizedAudio:
    return NormalizedAudio(
        np.broadcast_to(np.zeros(1, dtype=np.float32), seconds * SAMPLE_RATE)
    )


def install_whisper(monkeypatch, model_class) -> None:
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=model_class)
    )


def test_schema_six_uses_sample_coordinates_without_wav_path() -> None:
    assert parallel_asr.SCHEMA_VERSION == 6
    assert [field.name for field in fields(parallel_asr.AsrChunkPlan)] == [
        "index",
        "start_sample",
        "end_sample",
        "end_boundary",
        "estimated_speech_samples",
    ]


def test_schema_five_plan_is_rejected(workspace_tmp_path: Path) -> None:
    path = workspace_tmp_path / "asr_plan.json"
    write_json_atomic(path, {"schema_version": 5})
    with pytest.raises(ValueError, match="schema_version"):
        parallel_asr.load_plan(path)


def test_whisper_full_cache_skips_decode_vad_and_model(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr_parallel"
    options = TranscribeOptions(
        model="model-dir", language="zh", num_workers=1, cpu_threads=1
    )
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        runner, "decode_normalized_audio", lambda _path: fake_audio(120)
    )
    monkeypatch.setattr(runner, "detect_speech_intervals", lambda *_args: [])

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, samples, **kwargs):
            assert isinstance(samples, np.ndarray)
            assert kwargs["vad_filter"] is True
            return iter([]), types.SimpleNamespace(language="zh")

    install_whisper(monkeypatch, Model)
    _info, expected_segments, _source = runner.run_parallel_whisper_transcribe(
        audio_path, options, workspace
    )
    (workspace / "merged_transcript.json").unlink()
    (workspace / "metrics.json").unlink()

    monkeypatch.setattr(
        runner, "decode_normalized_audio", lambda *_args: pytest.fail("decoded")
    )
    monkeypatch.setattr(
        runner, "detect_speech_intervals", lambda *_args: pytest.fail("VAD")
    )
    install_whisper(
        monkeypatch,
        type(
            "Forbidden",
            (),
            {"__init__": lambda *_args, **_kwargs: pytest.fail("model")},
        ),
    )
    _info, resumed_segments, _source = runner.run_parallel_whisper_transcribe(
        audio_path, options, workspace
    )

    assert resumed_segments == expected_segments
    assert read_json(workspace / "merged_transcript.json") == {
        "segments": expected_segments
    }
    metrics = read_json(workspace / "metrics.json")
    assert metrics["chunk_count"] == 1
    assert metrics["segment_count"] == len(expected_segments)
    assert metrics["failed_chunks"] == []


def test_whisper_partial_resume_decodes_once_and_uses_concurrent_ndarray_slices(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr_parallel"
    options = TranscribeOptions(
        model="model-dir", language="zh", num_workers=2, cpu_threads=1
    )
    decoded = []
    inputs = []
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        runner,
        "decode_normalized_audio",
        lambda path: decoded.append(path) or fake_audio(360),
    )
    monkeypatch.setattr(runner, "detect_speech_intervals", lambda *_args: [])

    class Model:
        def __init__(self, *_args, **kwargs):
            assert kwargs["num_workers"] == 2

        def transcribe(self, samples, **kwargs):
            inputs.append((samples, kwargs))
            return iter([]), types.SimpleNamespace(language="zh")

    install_whisper(monkeypatch, Model)
    runner.run_parallel_whisper_transcribe(audio_path, options, workspace)

    assert len(decoded) == 1
    assert len(inputs) == 2
    assert all(
        isinstance(samples, np.ndarray) and kwargs["vad_filter"] is True
        for samples, kwargs in inputs
    )
    assert not (workspace / "chunks").exists()

    result_path = workspace / "chunk_results" / "chunk_001.json"
    result_path.unlink()
    inputs.clear()
    runner.run_parallel_whisper_transcribe(audio_path, options, workspace)
    assert len(decoded) == 2
    assert len(inputs) == 1


def test_whisper_chunk_result_uses_sample_identity(
    workspace_tmp_path: Path, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr_parallel"
    options = TranscribeOptions(
        model="model-dir", language="zh", num_workers=1, cpu_threads=1
    )
    monkeypatch.setattr(runner.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(runner, "decode_normalized_audio", lambda _path: fake_audio(25))
    monkeypatch.setattr(runner, "detect_speech_intervals", lambda *_args: [])

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            return iter([]), types.SimpleNamespace(language="zh")

    install_whisper(monkeypatch, Model)
    runner.run_parallel_whisper_transcribe(audio_path, options, workspace)
    result = read_json(workspace / "chunk_results" / "chunk_000.json")
    assert result["schema_version"] == 6
    assert {"start_sample", "end_sample"} <= result.keys()
    assert "chunk_audio_path" not in result


@pytest.mark.parametrize(
    "segments",
    [
        [{"end": 1.0, "text": "missing start"}],
        [{"start": 0.0, "end": 1.0}],
        [{"start": 0.0, "text": "missing end"}],
        [{"start": "0", "end": 1.0, "text": "non-numeric"}],
        [{"start": False, "end": 1.0, "text": "boolean"}],
        [{"start": 0.0, "end": 1.0, "text": 1}],
        [{"start": float("nan"), "end": 1.0, "text": "nan"}],
        [{"start": 0.0, "end": float("inf"), "text": "infinity"}],
        [{"start": -0.1, "end": 1.0, "text": "negative"}],
        [{"start": 0.0, "end": 26.0, "text": "past chunk"}],
        [{"start": 2.0, "end": 1.0, "text": "reversed"}],
        [
            {"start": 2.0, "end": 3.0, "text": "later"},
            {"start": 1.0, "end": 2.0, "text": "earlier"},
        ],
    ],
)
def test_invalid_whisper_chunk_segments_are_pending_for_retranscription(
    workspace_tmp_path: Path,
    segments: list[dict[str, object]],
) -> None:
    workspace = workspace_tmp_path / "asr_parallel"
    chunk = parallel_asr.AsrChunkPlan(
        index=0,
        start_sample=0,
        end_sample=25 * SAMPLE_RATE,
        end_boundary="audio_end",
        estimated_speech_samples=0,
    )
    plan = parallel_asr.ParallelAsrPlan(
        schema_version=parallel_asr.SCHEMA_VERSION,
        source_audio=parallel_asr.AsrSourceAudio(
            path="audio.m4a",
            size=5,
            mtime=1.0,
            sample_count=25 * SAMPLE_RATE,
        ),
        provider="whisper",
        model="model-dir",
        language="zh",
        beam_size=5,
        device="cpu",
        compute_type="float32",
        vad_parameters=parallel_asr.DEFAULT_VAD_PARAMETERS,
        planning_parameters=parallel_asr.DEFAULT_PLANNING_PARAMETERS,
        count_strategy="divisible",
        group_size=1,
        cpu_budget=3,
        num_workers=1,
        cpu_threads=1,
        chunks=[chunk],
    )
    result_path = workspace / "chunk_results" / "chunk_000.json"
    write_json_atomic(
        result_path,
        {
            "schema_version": parallel_asr.SCHEMA_VERSION,
            "chunk_index": 0,
            "start_sample": chunk.start_sample,
            "end_sample": chunk.end_sample,
            "end_boundary": chunk.end_boundary,
            "source": asdict(plan.source_audio),
            "plan": parallel_asr.plan_to_dict(plan),
            "model": {
                "path": plan.model,
                "language": plan.language,
                "beam_size": plan.beam_size,
                "device": plan.device,
                "compute_type": plan.compute_type,
                "cpu_threads": plan.cpu_threads,
                "num_workers": plan.num_workers,
            },
            "elapsed_seconds": 0.1,
            "segments": segments,
        },
    )

    valid_results = load_valid_chunk_results(workspace, plan)
    progress = prepare_progress_for_resume(
        plan,
        initial_progress(plan),
        set(valid_results),
    )

    assert valid_results == {}
    assert progress["chunks"]["chunk_000"]["status"] == "pending"
