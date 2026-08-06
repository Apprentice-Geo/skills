from __future__ import annotations

import argparse
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from scripts.asr.alignment import AlignmentContractError
from scripts.asr.chunking import ChunkLayout, NormalizedAudio
from scripts.asr.execution import Qwen3AsrCudaPolicy, WhisperCpuPolicy
from scripts.asr.providers import Qwen3AsrProvider, WhisperProvider
from scripts.config import QWEN3_ASR_DEVICE_MAP, QWEN3_ASR_DTYPE
from scripts.process_logging import LoggingSession, get_logger
from scripts.runtime_options import TranscribeOptions


@contextmanager
def isolated_logging_session(log_path):
    root_logger = get_logger()
    handlers = list(root_logger.handlers)
    level = root_logger.level
    propagate = root_logger.propagate
    try:
        with LoggingSession(log_path):
            yield
    finally:
        root_logger.handlers[:] = handlers
        root_logger.setLevel(level)
        root_logger.propagate = propagate


def test_whisper_provider_forces_word_timestamps_and_preserves_probability() -> None:
    calls = []
    model = SimpleNamespace(
        transcribe=lambda samples, **kwargs: (
            iter(
                [
                    SimpleNamespace(
                        text=" hello",
                        words=[
                            SimpleNamespace(
                                word=" hello", start=0.0, end=0.5, probability=0.8
                            )
                        ],
                    )
                ]
            ),
            SimpleNamespace(
                language="en",
                language_probability=1.0,
                duration=1.0,
                duration_after_vad=0.5,
            ),
        )
    )
    original = model.transcribe

    def capture(samples, **kwargs):
        calls.append(kwargs)
        return original(samples, **kwargs)

    model.transcribe = capture
    provider = WhisperProvider(TranscribeOptions(language="en", model_path="model"))
    transcript = provider.transcribe_one(
        model,
        np.zeros(16_000, dtype=np.float32),
        ChunkLayout(0, 0, 16_000, "audio_end", 1),
    )

    assert calls[0]["word_timestamps"] is True
    assert transcript.words[0].probability == 0.8


def test_qwen_provider_parses_probability_as_none() -> None:
    provider = Qwen3AsrProvider("zh")
    result = SimpleNamespace(
        text="你好",
        time_stamps=SimpleNamespace(
            items=[SimpleNamespace(text="你好", start_time=0.0, end_time=0.5)]
        ),
    )

    transcript = provider.parse_result(
        result, ChunkLayout(0, 0, 16_000, "audio_end", 1), 0.2
    )

    assert transcript.words[0].probability is None
    identity = provider.request_identity()
    assert identity["provider"] == "qwen3-asr"
    assert identity["max_new_tokens"] > 0
    assert identity["return_time_stamps"] is True


def test_whisper_prepare_logs_only_safe_model_configuration(
    workspace_tmp_path: Path, monkeypatch
) -> None:
    faster_whisper = ModuleType("faster_whisper")
    faster_whisper.WhisperModel = lambda *_args, **_kwargs: object()
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)
    provider = WhisperProvider(
        TranscribeOptions(
            language="en",
            model_path="custom-whisper",
            device="cpu",
            compute_type="int8",
        )
    )
    log_path = workspace_tmp_path / "whisper-prepare.log"

    with isolated_logging_session(log_path):
        provider.prepare(
            {
                "policy": "whisper-cpu",
                "num_workers": 2,
                "cpu_threads": 3,
            }
        )

    log_text = log_path.read_text(encoding="utf-8")
    assert (
        "provider=faster-whisper model=custom-whisper device=cpu compute_type=int8 "
        "policy=whisper-cpu num_workers=2 cpu_threads=3"
    ) in log_text


def test_qwen_prepare_logs_only_safe_model_configuration(
    workspace_tmp_path: Path, monkeypatch
) -> None:
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: True)
    setattr(torch, QWEN3_ASR_DTYPE, object())
    qwen_asr = ModuleType("qwen_asr")
    qwen_asr.Qwen3ASRModel = SimpleNamespace(
        from_pretrained=lambda *_args, **_kwargs: object()
    )
    transformers = ModuleType("transformers")
    transformers.GenerationConfig = SimpleNamespace(
        from_pretrained=lambda *_args, **_kwargs: object()
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "qwen_asr", qwen_asr)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setattr(
        "scripts.asr.providers.qwen3_asr.model_has_weights", lambda *_args: True
    )
    provider = Qwen3AsrProvider("zh")
    log_path = workspace_tmp_path / "qwen-prepare.log"

    with isolated_logging_session(log_path):
        provider.prepare({"policy": "qwen3-asr-cuda", "batch_size": 4})

    log_text = log_path.read_text(encoding="utf-8")
    assert "provider=qwen3-asr" in log_text
    assert f"device={QWEN3_ASR_DEVICE_MAP} compute_type={QWEN3_ASR_DTYPE}" in log_text
    assert "policy=qwen3-asr-cuda batch_size=4" in log_text


def test_qwen_provider_clips_small_last_word_end_overrun() -> None:
    provider = Qwen3AsrProvider("en")
    result = SimpleNamespace(
        text="hello later",
        time_stamps=SimpleNamespace(
            items=[
                SimpleNamespace(text="hello", start_time=0.0, end_time=0.5),
                SimpleNamespace(text="later", start_time=0.5, end_time=1.1),
            ]
        ),
    )

    transcript = provider.parse_result(
        result, ChunkLayout(0, 0, 16_000, "audio_end", 1), 0.2
    )

    assert transcript.words[-1].end == 1.0


def test_qwen_provider_rejects_large_last_word_end_overrun() -> None:
    provider = Qwen3AsrProvider("en")
    result = SimpleNamespace(
        text="hello",
        time_stamps=SimpleNamespace(
            items=[SimpleNamespace(text="hello", start_time=0.0, end_time=1.101)]
        ),
    )

    with pytest.raises(AlignmentContractError, match="invalid token time"):
        provider.parse_result(result, ChunkLayout(0, 0, 16_000, "audio_end", 1), 0.2)


def test_whisper_preserves_provider_text_in_returned_segment_copy() -> None:
    provider = WhisperProvider(TranscribeOptions(language="zh", model_path="model"))
    source = [{"id": 0, "start": 0.0, "end": 1.0, "text": "後臺"}]

    copied = provider.postprocess_segments(source)

    assert source[0]["text"] == "後臺"
    assert copied[0]["text"] == "後臺"
    assert copied is not source


def test_transcribe_options_from_args_reads_model_path() -> None:
    options = TranscribeOptions.from_args(
        argparse.Namespace(language="en", model_path="custom-whisper")
    )

    assert options.model_path == "custom-whisper"


def test_whisper_policy_logs_retry_traceback_then_succeeds(
    workspace_tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("scripts.asr.execution.whisper_cpu.os.cpu_count", lambda: 4)
    policy = WhisperCpuPolicy(TranscribeOptions(num_workers=1, cpu_threads=1))
    layout = ChunkLayout(0, 0, 16_000, "audio_end", 1)
    audio = NormalizedAudio(np.zeros(16_000, dtype=np.float32))
    provider = SimpleNamespace(name="whisper", prepare=lambda identity: object())
    calls = []

    def transcribe_one(*_args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("once")
        return "ok"

    provider.transcribe_one = transcribe_one
    cached = []
    log_path = workspace_tmp_path / "whisper-retry.log"
    with isolated_logging_session(log_path):
        failures = policy.execute(
            provider,
            audio,
            [layout],
            {"num_workers": 1, "cpu_threads": 1},
            cached.append,
        )

    assert failures == {}
    assert calls == [1, 1]
    assert cached == ["ok"]
    log_text = log_path.read_text(encoding="utf-8")
    assert (
        "provider=whisper policy=whisper-cpu chunk=chunk_000 attempt=1/2 action=retry"
    ) in log_text
    assert "Traceback (most recent call last)" in log_text
    assert "RuntimeError: once" in log_text


def test_whisper_policy_shares_one_prepared_model_across_workers(monkeypatch) -> None:
    monkeypatch.setattr("scripts.asr.execution.whisper_cpu.os.cpu_count", lambda: 4)
    policy = WhisperCpuPolicy(TranscribeOptions(num_workers=2, cpu_threads=1))
    audio = NormalizedAudio(np.zeros(32_000, dtype=np.float32))
    layouts = [
        ChunkLayout(0, 0, 16_000, "audio", 1),
        ChunkLayout(1, 16_000, 32_000, "audio_end", 1),
    ]
    model = object()
    provider = SimpleNamespace(name="whisper", prepare=lambda _identity: model)
    barrier = threading.Barrier(2)
    seen_models = []

    def transcribe_one(prepared, _samples, _layout):
        seen_models.append(prepared)
        barrier.wait(timeout=2)
        return "ok"

    provider.transcribe_one = transcribe_one
    cached = []
    failures = policy.execute(
        provider,
        audio,
        layouts,
        {"num_workers": 2, "cpu_threads": 1},
        cached.append,
    )

    assert failures == {}
    assert seen_models == [model, model]
    assert cached == ["ok", "ok"]


def test_whisper_policy_returns_final_exception_and_logs_second_traceback(
    workspace_tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("scripts.asr.execution.whisper_cpu.os.cpu_count", lambda: 4)
    policy = WhisperCpuPolicy(TranscribeOptions(num_workers=1, cpu_threads=1))
    layout = ChunkLayout(0, 0, 16_000, "audio_end", 1)
    audio = NormalizedAudio(np.zeros(16_000, dtype=np.float32))
    provider = SimpleNamespace(
        name="whisper",
        prepare=lambda identity: object(),
        transcribe_one=lambda *_args: (_ for _ in ()).throw(RuntimeError("permanent")),
    )
    log_path = workspace_tmp_path / "whisper-failure.log"

    with isolated_logging_session(log_path):
        failures = policy.execute(
            provider,
            audio,
            [layout],
            {"num_workers": 1, "cpu_threads": 1},
            lambda _transcript: None,
        )

    failure = failures["chunk_000"]
    assert isinstance(failure, RuntimeError)
    assert str(failure) == "permanent"
    log_text = log_path.read_text(encoding="utf-8")
    assert "attempt=1/2 action=retry" in log_text
    assert "attempt=2/2 action=fail" in log_text
    assert log_text.count("Traceback (most recent call last)") == 2


def test_qwen_policy_logs_batch_and_isolated_failure_tracebacks(
    workspace_tmp_path,
) -> None:
    policy = Qwen3AsrCudaPolicy()
    layouts = [
        ChunkLayout(0, 0, 1, "silence", 0),
        ChunkLayout(1, 1, 2, "audio_end", 0),
    ]
    audio = NormalizedAudio(np.zeros(2, dtype=np.float32))
    provider = SimpleNamespace(name="qwen3-asr", prepare=lambda identity: object())
    provider.transcribe_batch = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("batch")
    )

    def one(_model, _samples, layout):
        if layout.index == 1:
            raise RuntimeError("bad")
        return SimpleNamespace(chunk_index=layout.index)

    provider.transcribe_one = one
    cached = []
    log_path = workspace_tmp_path / "qwen-failure.log"
    with isolated_logging_session(log_path):
        failures = policy.execute(
            provider,
            audio,
            layouts,
            {"batch_size": 2},
            cached.append,
        )

    assert [item.chunk_index for item in cached] == [0]
    assert isinstance(failures["chunk_001"], RuntimeError)
    assert str(failures["chunk_001"]) == "bad"
    log_text = log_path.read_text(encoding="utf-8")
    assert (
        "provider=qwen3-asr policy=qwen3-asr-cuda batch=1 "
        "chunks=chunk_000..chunk_001 attempt=batch action=isolate"
    ) in log_text
    assert (
        "provider=qwen3-asr policy=qwen3-asr-cuda batch=1 "
        "chunk=chunk_001 attempt=isolation action=fail"
    ) in log_text
    assert "RuntimeError: batch" in log_text
    assert "RuntimeError: bad" in log_text
    assert log_text.count("Traceback (most recent call last)") == 2
