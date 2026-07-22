from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.asr.alignment import AlignmentContractError
from scripts.asr.chunking import ChunkLayout, NormalizedAudio
from scripts.asr.execution import Qwen3CudaPolicy, WhisperCpuPolicy
from scripts.asr.providers import Qwen3Provider, WhisperProvider
from scripts.asr.providers.qwen3 import has_model_weights
from scripts.runtime_options import TranscribeOptions


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
    provider = WhisperProvider(TranscribeOptions(language="en", model="model"))
    transcript = provider.transcribe_one(
        model,
        np.zeros(16_000, dtype=np.float32),
        ChunkLayout(0, 0, 16_000, "audio_end", 1),
    )

    assert calls[0]["word_timestamps"] is True
    assert transcript.words[0].probability == 0.8


def test_qwen_provider_parses_probability_as_none() -> None:
    provider = Qwen3Provider("zh")
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
    assert identity["max_new_tokens"] > 0
    assert identity["return_time_stamps"] is True


def test_qwen_model_weights_accept_sharded_safetensors(
    workspace_tmp_path: Path,
) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model-00001-of-00002.safetensors").write_bytes(b"weights")

    assert has_model_weights(model_dir)


def test_qwen_provider_clips_small_last_word_end_overrun() -> None:
    provider = Qwen3Provider("en")
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
    provider = Qwen3Provider("en")
    result = SimpleNamespace(
        text="hello",
        time_stamps=SimpleNamespace(
            items=[SimpleNamespace(text="hello", start_time=0.0, end_time=1.101)]
        ),
    )

    with pytest.raises(AlignmentContractError, match="invalid token time"):
        provider.parse_result(result, ChunkLayout(0, 0, 16_000, "audio_end", 1), 0.2)


def test_whisper_chinese_conversion_only_changes_returned_segment_copy(
    monkeypatch,
) -> None:
    provider = WhisperProvider(TranscribeOptions(language="zh", model="model"))
    source = [{"id": 0, "start": 0.0, "end": 1.0, "text": "後臺"}]
    monkeypatch.setattr(
        "scripts.asr.providers.whisper.make_simplified_chinese_converter",
        lambda: SimpleNamespace(convert=lambda text: text.replace("後臺", "后台")),
    )

    converted = provider.postprocess_segments(source)

    assert source[0]["text"] == "後臺"
    assert converted[0]["text"] == "后台"


def test_whisper_policy_retries_each_failed_chunk_once(monkeypatch) -> None:
    monkeypatch.setattr("scripts.asr.execution.whisper_cpu.os.cpu_count", lambda: 4)
    policy = WhisperCpuPolicy(TranscribeOptions(num_workers=1, cpu_threads=1))
    layout = ChunkLayout(0, 0, 16_000, "audio_end", 1)
    audio = NormalizedAudio(np.zeros(16_000, dtype=np.float32))
    provider = SimpleNamespace(prepare=lambda identity: object())
    calls = []

    def transcribe_one(*_args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("once")
        return "ok"

    provider.transcribe_one = transcribe_one
    cached = []
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


def test_qwen_policy_isolates_failed_batch_and_caches_successes() -> None:
    policy = Qwen3CudaPolicy()
    layouts = [
        ChunkLayout(0, 0, 1, "silence", 0),
        ChunkLayout(1, 1, 2, "audio_end", 0),
    ]
    audio = NormalizedAudio(np.zeros(2, dtype=np.float32))
    provider = SimpleNamespace(prepare=lambda identity: object())
    provider.transcribe_batch = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("batch")
    )

    def one(_model, _samples, layout):
        if layout.index == 1:
            raise RuntimeError("bad")
        return SimpleNamespace(chunk_index=layout.index)

    provider.transcribe_one = one
    cached = []
    failures = policy.execute(
        provider,
        audio,
        layouts,
        {"batch_size": 2},
        cached.append,
    )

    assert [item.chunk_index for item in cached] == [0]
    assert failures == {"chunk_001": "bad"}
