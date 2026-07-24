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
from scripts.process_logging import LoggingSession
from scripts.utils import read_json, write_json


class FakeProvider:
    name = "fake"
    source = "fake-asr"
    language = "en"

    def __init__(self, request_version: int = 1) -> None:
        self.request_version = request_version
        self.prepare_calls = 0
        self.transcribed_indices: list[int] = []

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
        self.transcribed_indices.append(layout.index)
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
    name = "fake"

    def __init__(
        self,
        version: int = 1,
        fail_index: int | None = None,
        planning_parameters: PlanningParameters | None = None,
        layout_from_speech: bool = False,
    ) -> None:
        self.version = version
        self.fail_index = fail_index
        self.planning_parameters = planning_parameters or PlanningParameters(1, 2)
        self.layout_from_speech = layout_from_speech

    def execution_identity(self, sample_count: int) -> dict[str, Any]:
        return {"policy": "fake", "version": self.version, "sample_count": sample_count}

    def layouts(
        self, sample_count: int, speech: Any, identity: Any
    ) -> tuple[ChunkLayout, ...]:
        del identity
        assert sample_count == 4
        if self.layout_from_speech and speech == [(0, 1)]:
            return (
                ChunkLayout(0, 0, 1, "silence", 1),
                ChunkLayout(1, 1, 4, "audio_end", 0),
            )
        return (
            ChunkLayout(0, 0, 2, "silence", 1),
            ChunkLayout(1, 2, 4, "audio_end", 1),
        )

    def execute(
        self, provider: Any, audio: Any, pending: Any, identity: Any, cache: Any
    ) -> dict[str, BaseException]:
        model = provider.prepare(identity)
        failures: dict[str, BaseException] = {}
        for layout in pending:
            if layout.index == self.fail_index:
                failures[f"chunk_{layout.index:03d}"] = RuntimeError("failed")
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


def test_missing_vad_is_regenerated_without_loading_model_when_layout_is_unchanged(
    workspace_tmp_path: Path, fake_audio, monkeypatch, caplog
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    (workspace / "vad_result.json").unlink()
    vad_calls = []
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples",
        lambda *_args: vad_calls.append(1) or [(0, 4)],
    )
    provider = FakeProvider()
    caplog.set_level("WARNING")

    run_asr_pipeline(audio_path, workspace, provider, FakePolicy())

    assert vad_calls == [1]
    assert (workspace / "vad_result.json").is_file()
    assert provider.prepare_calls == 0
    assert "reason=missing" in caplog.text


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (
            lambda payload: payload.update(parameters={"threshold": 0.9}),
            "parameters_mismatch",
        ),
        (
            lambda payload: payload.update(
                speech_intervals=[{"start_sample": True, "end_sample": 4}]
            ),
            "invalid_structure",
        ),
    ],
)
def test_invalid_vad_reason_is_logged_and_artifact_is_repaired(
    workspace_tmp_path: Path,
    fake_audio,
    monkeypatch,
    caplog,
    mutate,
    expected_reason: str,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    vad_path = workspace / "vad_result.json"
    payload = read_json(vad_path)
    mutate(payload)
    write_json(vad_path, payload)
    vad_calls = []
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples",
        lambda *_args: vad_calls.append(1) or [(0, 4)],
    )
    caplog.set_level("WARNING")

    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())

    assert vad_calls == [1]
    assert read_json(vad_path)["parameters"]["threshold"] == 0.35
    assert f"reason={expected_reason}" in caplog.text


def test_regenerated_vad_layout_change_invalidates_all_chunk_results(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    policy = FakePolicy(
        planning_parameters=PlanningParameters(1, 3),
        layout_from_speech=True,
    )
    run_asr_pipeline(audio_path, workspace, FakeProvider(), policy)
    (workspace / "vad_result.json").unlink()
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples", lambda *_: [(0, 1)]
    )
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, policy)

    assert provider.transcribed_indices == [0, 1]
    chunks = read_json(workspace / "asr_plan.json")["chunks"]
    assert [(chunk["start_sample"], chunk["end_sample"]) for chunk in chunks] == [
        (0, 1),
        (1, 4),
    ]


def test_valid_vad_layout_mismatch_rebuilds_plan_and_rejects_old_results(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    policy = FakePolicy(planning_parameters=PlanningParameters(1, 3))
    run_asr_pipeline(audio_path, workspace, FakeProvider(), policy)
    plan = read_json(workspace / "asr_plan.json")
    plan["chunks"][0].update(end_sample=1)
    plan["chunks"][1].update(start_sample=1)
    write_json(workspace / "asr_plan.json", plan)
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, policy)

    assert provider.transcribed_indices == [0, 1]
    chunks = read_json(workspace / "asr_plan.json")["chunks"]
    assert [(chunk["start_sample"], chunk["end_sample"]) for chunk in chunks] == [
        (0, 2),
        (2, 4),
    ]


def test_partial_cache_with_valid_vad_does_not_repeat_vad(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    (workspace / "chunk_results" / "chunk_001.json").unlink()
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples",
        lambda *_: pytest.fail("VAD repeated for valid partial cache"),
    )
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, FakePolicy())

    assert provider.transcribed_indices == [1]


def test_decoded_sample_count_change_reruns_vad_and_invalidates_results(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    class FlexiblePolicy(FakePolicy):
        def layouts(self, sample_count, speech, identity):
            del speech, identity
            if sample_count == 5:
                return (
                    ChunkLayout(0, 0, 2, "silence", 2),
                    ChunkLayout(1, 2, 5, "audio_end", 3),
                )
            return super().layouts(sample_count, [], {})

    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    policy = FlexiblePolicy(planning_parameters=PlanningParameters(1, 3))
    run_asr_pipeline(audio_path, workspace, FakeProvider(), policy)
    (workspace / "chunk_results" / "chunk_001.json").unlink()
    monkeypatch.setattr(
        "scripts.asr.pipeline.decode_normalized_audio",
        lambda _path: NormalizedAudio(np.zeros(5, dtype=np.float32)),
    )
    vad_calls = []
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples",
        lambda *_args: vad_calls.append(1) or [(0, 5)],
    )
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, policy)

    assert vad_calls == [1]
    assert provider.transcribed_indices == [0, 1]
    assert read_json(workspace / "asr_plan.json")["source"]["sample_count"] == 5


def test_failure_keeps_successful_chunks_and_does_not_write_result(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    log_path = workspace_tmp_path / "single-failure.log"

    with LoggingSession(log_path):
        with pytest.raises(
            RuntimeError, match="chunk_001: RuntimeError: failed"
        ) as exc_info:
            run_asr_pipeline(
                audio_path, workspace, FakeProvider(), FakePolicy(fail_index=1)
            )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "failed"
    progress = read_json(workspace / "progress.json")
    assert progress["chunks"]["chunk_001"]["error"] == "RuntimeError: failed"
    assert (workspace / "chunk_results" / "chunk_000.json").is_file()
    assert not (workspace / "result.json").exists()
    assert "ASR pipeline failure: provider=fake policy=fake" in log_path.read_text(
        encoding="utf-8"
    )


def test_multiple_failures_have_summaries_without_fake_single_cause(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    log_path = workspace_tmp_path / "multiple-failures.log"

    class AllFailPolicy(FakePolicy):
        def execute(
            self, provider: Any, audio: Any, pending: Any, identity: Any, cache: Any
        ) -> dict[str, BaseException]:
            del provider, audio, identity, cache
            return {
                f"chunk_{layout.index:03d}": ValueError(f"failure-{layout.index}")
                for layout in pending
            }

    with LoggingSession(log_path):
        with pytest.raises(RuntimeError) as exc_info:
            run_asr_pipeline(audio_path, workspace, FakeProvider(), AllFailPolicy())

    assert exc_info.value.__cause__ is None
    assert str(exc_info.value) == (
        "ASR chunks failed after retry/isolation: "
        "chunk_000: ValueError: failure-0; chunk_001: ValueError: failure-1"
    )
    progress = read_json(workspace / "progress.json")
    assert progress["chunks"]["chunk_000"]["error"] == "ValueError: failure-0"
    assert progress["chunks"]["chunk_001"]["error"] == "ValueError: failure-1"
    log_text = log_path.read_text(encoding="utf-8")
    assert "chunk_000: ValueError: failure-0" in log_text
    assert "chunk_001: ValueError: failure-1" in log_text


def test_pipeline_logs_safe_stage_context_without_transcript_text(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    log_path = workspace_tmp_path / "asr.log"

    with LoggingSession(log_path):
        run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())

    log_text = log_path.read_text(encoding="utf-8")
    assert (
        f"ASR pipeline start: provider=fake policy=fake workspace={workspace}"
        in log_text
    )
    assert "ASR plan cache: status=miss" in log_text
    assert "ASR audio decode: start" in log_text
    assert "ASR audio decode: complete samples=4" in log_text
    assert "ASR VAD: complete intervals=1 speech_samples=4" in log_text
    assert "ASR plan: status=created chunks=2" in log_text
    assert "[Transcribe] cache: reused=0, pending=2, total=2" in log_text
    assert "ASR execution: start provider=fake policy=fake pending=2" in log_text
    assert "ASR merge: complete chunks=2 segments=" in log_text
    assert "ASR pipeline success: provider=fake policy=fake" in log_text
    assert "one" not in log_text
    assert "two" not in log_text
