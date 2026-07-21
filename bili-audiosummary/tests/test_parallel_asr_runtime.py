from __future__ import annotations

import sys
import types
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from scripts.asr import parallel as parallel_asr
from scripts.asr.chunking import SAMPLE_RATE, NormalizedAudio
from scripts.asr.parallel import runner
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
    runner.run_parallel_whisper_transcribe(audio_path, options, workspace)

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
    runner.run_parallel_whisper_transcribe(audio_path, options, workspace)


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
