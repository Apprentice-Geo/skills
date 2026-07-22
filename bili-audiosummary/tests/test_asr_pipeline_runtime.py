from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from scripts.asr.alignment import TranscriptWord
from scripts.asr.chunking import (
    ChunkLayout,
    NormalizedAudio,
    PlanningParameters,
    VadParameters,
)
from scripts.asr.pipeline import run_asr_pipeline
from scripts.asr.pipeline_types import ChunkTranscript
from scripts.utils import read_json, write_json


class FakeProvider:
    name = "fake"
    source = "fake-asr"
    language = "en"

    def __init__(self, request_version: int = 1) -> None:
        self.request_version = request_version
        self.prepare_calls = 0

    def request_identity(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "language": self.language,
            "version": self.request_version,
        }

    def prepare(self, execution_identity: dict[str, Any]) -> object:
        self.prepare_calls += 1
        return object()

    def transcribe_one(
        self, prepared: Any, samples: Any, layout: ChunkLayout
    ) -> ChunkTranscript:
        del prepared, samples
        text = "one" if layout.index == 0 else "two"
        return ChunkTranscript(
            layout.index,
            layout.start_sample,
            layout.end_sample,
            text,
            (TranscriptWord(text, 0.0, 0.0001, 0.75),),
            {"provider": "fake"},
            0.1,
        )

    def final_info(self, plan: Any, words_present: bool) -> dict[str, Any]:
        return {"language": self.language, "word_timestamps": words_present}

    def postprocess_segments(
        self, segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [{**segment} for segment in segments]


class FakePolicy:
    def __init__(
        self,
        version: int = 1,
        fail_index: int | None = None,
        planning_parameters: PlanningParameters | None = None,
    ) -> None:
        self.version = version
        self.fail_index = fail_index
        self.planning_parameters = planning_parameters or PlanningParameters(1, 2)

    def execution_identity(self, sample_count: int) -> dict[str, Any]:
        return {"policy": "fake", "version": self.version, "sample_count": sample_count}

    def layouts(
        self, sample_count: int, speech: Any, identity: Any
    ) -> tuple[ChunkLayout, ...]:
        del speech, identity
        assert sample_count == 4
        return (
            ChunkLayout(0, 0, 2, "silence", 1),
            ChunkLayout(1, 2, 4, "audio_end", 1),
        )

    def execute(
        self, provider: Any, audio: Any, pending: Any, identity: Any, cache: Any
    ) -> dict[str, str]:
        model = provider.prepare(identity)
        failures = {}
        for layout in pending:
            if layout.index == self.fail_index:
                failures[f"chunk_{layout.index:03d}"] = "failed"
            else:
                cache(provider.transcribe_one(model, audio.slice(layout), layout))
        return failures


@pytest.fixture
def fake_audio(monkeypatch):
    audio = NormalizedAudio(np.zeros(4, dtype=np.float32))
    decode = SimpleNamespace(calls=0)

    def load(_path: Path) -> NormalizedAudio:
        decode.calls += 1
        return audio

    monkeypatch.setattr("scripts.asr.pipeline.decode_normalized_audio", load)
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples", lambda *_: [(0, 4)]
    )
    return decode


def test_pipeline_writes_unified_artifacts_and_replays_without_decode(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    provider = FakeProvider()

    info, segments, source = run_asr_pipeline(
        audio_path, workspace, provider, FakePolicy()
    )

    assert (workspace / "asr_plan.json").is_file()
    assert (workspace / "vad_result.json").is_file()
    assert (workspace / "progress.json").is_file()
    assert (workspace / "metrics.json").is_file()
    assert read_json(workspace / "result.json")["words"][1]["start"] == pytest.approx(
        0.0, abs=0.001
    )
    assert (
        read_json(workspace / "chunk_results" / "chunk_000.json")["words"][0][
            "probability"
        ]
        == 0.75
    )
    assert info["word_timestamps"] is True
    assert "".join(segment["text"] for segment in segments).replace(" ", "") == "onetwo"
    assert source == "fake-asr"
    assert fake_audio.calls == 1
    assert provider.prepare_calls == 1

    monkeypatch.setattr(
        "scripts.asr.pipeline.decode_normalized_audio",
        lambda _path: pytest.fail("audio decoded on complete cache hit"),
    )
    run_asr_pipeline(audio_path, workspace, provider, FakePolicy())
    assert provider.prepare_calls == 1


@pytest.mark.parametrize("change", ["request", "execution", "planning", "vad"])
def test_identity_change_invalidates_cache(
    workspace_tmp_path: Path, fake_audio, monkeypatch, change: str
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())

    provider = FakeProvider(2 if change == "request" else 1)
    policy = FakePolicy(
        2 if change == "execution" else 1,
        planning_parameters=(
            PlanningParameters(1, 3) if change == "planning" else None
        ),
    )
    if change == "vad":
        monkeypatch.setattr(
            "scripts.asr.pipeline.DEFAULT_VAD_PARAMETERS",
            VadParameters(threshold=0.5),
        )
    run_asr_pipeline(audio_path, workspace, provider, policy)

    assert fake_audio.calls == 2
    assert provider.prepare_calls == 1


def test_corrupt_chunk_is_recomputed_but_valid_chunk_is_reused(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    payload = read_json(workspace / "chunk_results" / "chunk_001.json")
    payload["words"][0]["end"] = 9.0
    write_json(workspace / "chunk_results" / "chunk_001.json", payload)
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, FakePolicy())

    assert provider.prepare_calls == 1
    assert (
        read_json(workspace / "progress.json")["chunks"]["chunk_000"]["status"]
        == "succeeded"
    )


def test_old_schema_is_rejected_and_rebuilt(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    plan = read_json(workspace / "asr_plan.json")
    plan["schema_version"] = 6
    write_json(workspace / "asr_plan.json", plan)

    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())

    assert fake_audio.calls == 2
    assert read_json(workspace / "asr_plan.json")["schema_version"] == 1


def test_corrupt_merged_result_is_rebuilt_from_chunks_without_decode(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    result = read_json(workspace / "result.json")
    result["segments"][0]["text"] = "corrupt"
    write_json(workspace / "result.json", result)
    monkeypatch.setattr(
        "scripts.asr.pipeline.decode_normalized_audio",
        lambda _path: pytest.fail("audio decoded while rebuilding merged result"),
    )

    _info, segments, _source = run_asr_pipeline(
        audio_path, workspace, FakeProvider(), FakePolicy()
    )

    assert "".join(item["text"] for item in segments).replace(" ", "") == "onetwo"
    assert "corrupt" not in str(read_json(workspace / "result.json")["segments"])


def test_failure_keeps_successful_chunks_and_does_not_write_result(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"

    with pytest.raises(RuntimeError, match="chunk_001"):
        run_asr_pipeline(
            audio_path, workspace, FakeProvider(), FakePolicy(fail_index=1)
        )

    assert (workspace / "chunk_results" / "chunk_000.json").is_file()
    assert not (workspace / "result.json").exists()
