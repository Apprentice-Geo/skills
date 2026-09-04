from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from scripts.asr.alignment import AlignmentContractError, TranscriptWord
from scripts.asr.chunking import (
    ChunkLayout,
    NormalizedAudio,
    PlanningParameters,
    VadParameters,
)
from scripts.asr.pipeline import run_asr_pipeline as _run_asr_pipeline
from scripts.asr.pipeline_types import ChunkTranscript
from scripts.io_utils import sha256_file
from scripts.process_logging import LoggingSession
from scripts.utils import read_json, write_json

FAKE_SPLIT = 16_000
FAKE_SAMPLE_COUNT = 32_000
CHANGED_SAMPLE_COUNT = 48_000
TEST_VARIANT_ID = "b" * 64


def run_asr_pipeline(audio_path, workspace, provider, policy, **kwargs):
    return _run_asr_pipeline(
        audio_path,
        workspace,
        provider,
        policy,
        audio_id=sha256_file(audio_path),
        variant_id=TEST_VARIANT_ID,
        **kwargs,
    )


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
            (TranscriptWord(text, 0.0, layout.sample_count / 16_000, 0.75),),
            {"provider": "fake"},
            0.1,
        )

    def final_info(self, plan: Any, words_present: bool) -> dict[str, Any]:
        return {"language": self.language, "word_timestamps": words_present}


class ZeroDurationProvider(FakeProvider):
    def transcribe_one(
        self, prepared: Any, samples: Any, layout: ChunkLayout
    ) -> ChunkTranscript:
        if layout.index != 0:
            return super().transcribe_one(prepared, samples, layout)
        del prepared, samples
        self.transcribed_indices.append(layout.index)
        return ChunkTranscript(
            layout.index,
            layout.start_sample,
            layout.end_sample,
            "echo echo",
            (
                TranscriptWord("echo", 0.1001, 0.1004, 0.9),
                TranscriptWord("echo", 0.1004, 0.5, 0.8),
            ),
            {"provider": "fake"},
            0.1,
        )


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
        self.planning_parameters = planning_parameters or PlanningParameters(
            1, FAKE_SAMPLE_COUNT
        )
        self.layout_from_speech = layout_from_speech

    def execution_identity(self, sample_count: int) -> dict[str, Any]:
        return {"policy": "fake", "version": self.version, "sample_count": sample_count}

    def layouts(
        self, sample_count: int, speech: Any, identity: Any
    ) -> tuple[ChunkLayout, ...]:
        del identity
        assert sample_count == FAKE_SAMPLE_COUNT
        if self.layout_from_speech and speech == [(0, 8_000)]:
            return (
                ChunkLayout(0, 0, 8_000, "silence", 8_000),
                ChunkLayout(1, 8_000, FAKE_SAMPLE_COUNT, "audio_end", 0),
            )
        return (
            ChunkLayout(0, 0, FAKE_SPLIT, "silence", 1),
            ChunkLayout(1, FAKE_SPLIT, FAKE_SAMPLE_COUNT, "audio_end", 1),
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
    audio = NormalizedAudio(np.zeros(FAKE_SAMPLE_COUNT, dtype=np.float32))
    decode = SimpleNamespace(calls=0)

    def load(_path: Path) -> NormalizedAudio:
        decode.calls += 1
        return audio

    monkeypatch.setattr("scripts.asr.pipeline.decode_normalized_audio", load)
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples",
        lambda *_: [(0, FAKE_SAMPLE_COUNT)],
    )
    return decode


def test_pipeline_writes_unified_artifacts_and_replays_without_decode(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    provider = FakeProvider()

    outcome = run_asr_pipeline(audio_path, workspace, provider, FakePolicy())

    assert (workspace / "asr_plan.json").is_file()
    assert (workspace / "vad_result.json").is_file()
    assert not (workspace / "progress.json").exists()
    assert not (workspace / "metrics.json").exists()
    result = read_json(workspace / "result.json")
    assert set(result) == {
        "audio_id",
        "variant_id",
        "text",
        "items",
        "duration",
        "provider",
        "language",
    }
    assert result["items"][1]["start"] == pytest.approx(1.0, abs=0.001)
    assert result["text"] == "one two"
    assert (
        read_json(workspace / "chunk_results" / "chunk_000.json")["items"][0][
            "probability"
        ]
        == 0.75
    )
    assert outcome.final_info["word_timestamps"] is True
    assert outcome.source == "fake-asr"
    assert outcome.metrics.chunk_count == 2
    assert fake_audio.calls == 1
    assert provider.prepare_calls == 1

    monkeypatch.setattr(
        "scripts.asr.pipeline.decode_normalized_audio",
        lambda _path: pytest.fail("audio decoded on complete cache hit"),
    )
    cached = run_asr_pipeline(audio_path, workspace, provider, FakePolicy())
    assert provider.prepare_calls == 1
    assert cached.metrics.chunk_elapsed_seconds == ()


def test_pipeline_does_not_modify_legacy_progress_or_metrics(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    workspace.mkdir()
    progress = workspace / "progress.json"
    metrics = workspace / "metrics.json"
    progress.write_bytes(b"legacy progress")
    metrics.write_bytes(b"legacy metrics")

    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())

    assert progress.read_bytes() == b"legacy progress"
    assert metrics.read_bytes() == b"legacy metrics"


def test_pipeline_rejects_caller_audio_identity_mismatch(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")

    with pytest.raises(ValueError, match="expected audio identity"):
        _run_asr_pipeline(
            audio_path,
            workspace_tmp_path / "asr",
            FakeProvider(),
            FakePolicy(),
            audio_id="f" * 64,
            variant_id=TEST_VARIANT_ID,
        )


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
            PlanningParameters(1, CHANGED_SAMPLE_COUNT)
            if change == "planning"
            else None
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
    payload["items"][0]["end"] = 9.0
    write_json(workspace / "chunk_results" / "chunk_001.json", payload)
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, FakePolicy())

    assert provider.prepare_calls == 1
    assert provider.transcribed_indices == [1]
    assert not (workspace / "progress.json").exists()


def test_old_schema_is_rejected_and_rebuilt(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    plan = read_json(workspace / "asr_plan.json")
    plan["schema_version"] = 6
    write_json(workspace / "asr_plan.json", plan)
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples",
        lambda *_: pytest.fail("matching VAD cache was not reused"),
    )

    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())

    assert fake_audio.calls == 2
    rebuilt = read_json(workspace / "asr_plan.json")
    assert "schema_version" not in rebuilt
    assert len(rebuilt["plan_id"]) == 64


@pytest.mark.parametrize("remove", [False, True])
def test_result_is_rebuilt_from_chunks_without_decode_or_provider(
    workspace_tmp_path: Path, fake_audio, monkeypatch, remove: bool
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    result_path = workspace / "result.json"
    if remove:
        result_path.unlink()
    else:
        result = read_json(result_path)
        result["items"][0]["text"] = "corrupt"
        write_json(result_path, result)
    monkeypatch.setattr(
        "scripts.asr.pipeline.decode_normalized_audio",
        lambda _path: pytest.fail("audio decoded while rebuilding merged result"),
    )

    provider = FakeProvider()
    run_asr_pipeline(audio_path, workspace, provider, FakePolicy())

    assert read_json(result_path)["text"] == "one two"
    assert "corrupt" not in str(read_json(result_path)["items"])
    assert provider.prepare_calls == 0


def test_missing_vad_is_ignored_when_plan_is_valid(
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
        lambda *_args: vad_calls.append(1) or [(0, FAKE_SAMPLE_COUNT)],
    )
    provider = FakeProvider()
    caplog.set_level("WARNING")
    caplog.clear()

    run_asr_pipeline(audio_path, workspace, provider, FakePolicy())

    assert vad_calls == []
    assert not (workspace / "vad_result.json").exists()
    assert provider.prepare_calls == 0
    assert "ASR VAD cache invalid" not in caplog.text


def test_prepared_vad_is_ignored_when_plan_is_valid(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())
    (workspace / "vad_result.json").unlink()

    run_asr_pipeline(
        audio_path,
        workspace,
        FakeProvider(),
        FakePolicy(),
        prepared_vad=[(0, 8_000)],
    )

    assert not (workspace / "vad_result.json").exists()


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (
            lambda payload: payload.update(parameters={"threshold": 0.9}),
            "parameters_mismatch",
        ),
        (
            lambda payload: payload.update(
                speech_intervals=[
                    {"start_sample": True, "end_sample": FAKE_SAMPLE_COUNT}
                ]
            ),
            "invalid_structure",
        ),
    ],
)
def test_invalid_vad_is_ignored_when_plan_is_valid(
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
        lambda *_args: vad_calls.append(1) or [(0, FAKE_SAMPLE_COUNT)],
    )
    caplog.set_level("WARNING")

    run_asr_pipeline(audio_path, workspace, FakeProvider(), FakePolicy())

    assert vad_calls == []
    assert read_json(vad_path) == payload
    assert f"reason={expected_reason}" not in caplog.text


def test_missing_vad_cannot_invalidate_a_valid_plan(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    policy = FakePolicy(
        planning_parameters=PlanningParameters(1, FAKE_SAMPLE_COUNT),
        layout_from_speech=True,
    )
    run_asr_pipeline(audio_path, workspace, FakeProvider(), policy)
    (workspace / "vad_result.json").unlink()
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples", lambda *_: [(0, 8_000)]
    )
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, policy)

    assert provider.transcribed_indices == []
    chunks = read_json(workspace / "asr_plan.json")["chunks"]
    assert [(chunk["start_sample"], chunk["end_sample"]) for chunk in chunks] == [
        (0, FAKE_SPLIT),
        (FAKE_SPLIT, FAKE_SAMPLE_COUNT),
    ]


def test_valid_vad_layout_mismatch_rebuilds_plan_and_rejects_old_results(
    workspace_tmp_path: Path, fake_audio
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    policy = FakePolicy(planning_parameters=PlanningParameters(1, FAKE_SAMPLE_COUNT))
    run_asr_pipeline(audio_path, workspace, FakeProvider(), policy)
    plan = read_json(workspace / "asr_plan.json")
    plan["chunks"][0].update(end_sample=8_000)
    plan["chunks"][1].update(start_sample=8_000)
    write_json(workspace / "asr_plan.json", plan)
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, policy)

    assert provider.transcribed_indices == [0, 1]
    chunks = read_json(workspace / "asr_plan.json")["chunks"]
    assert [(chunk["start_sample"], chunk["end_sample"]) for chunk in chunks] == [
        (0, FAKE_SPLIT),
        (FAKE_SPLIT, FAKE_SAMPLE_COUNT),
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


def test_cleanup_report_is_not_persisted_or_replayed(
    workspace_tmp_path: Path, fake_audio
) -> None:
    class DuplicateCallbackPolicy(FakePolicy):
        def execute(self, provider, audio, pending, identity, cache):
            model = provider.prepare(identity)
            for layout in pending:
                transcript = provider.transcribe_one(model, audio.slice(layout), layout)
                cache(transcript)
                if layout.index == 0:
                    cache(transcript)
            return {}

    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    first_log = workspace_tmp_path / "first.log"

    with LoggingSession(first_log):
        run_asr_pipeline(
            audio_path,
            workspace,
            ZeroDurationProvider(),
            DuplicateCallbackPolicy(),
        )

    warning = "ASR timestamp cleanup: provider=fake chunk=chunk_000"
    assert first_log.read_text(encoding="utf-8").count(warning) == 1
    payload = read_json(workspace / "chunk_results" / "chunk_000.json")
    assert payload["text"] == " echo"
    assert set(payload) == {"plan_id", "chunk_index", "text", "items"}

    (workspace / "chunk_results" / "chunk_001.json").unlink()
    partial_log = workspace_tmp_path / "partial.log"
    with LoggingSession(partial_log):
        run_asr_pipeline(audio_path, workspace, ZeroDurationProvider(), FakePolicy())

    assert partial_log.read_text(encoding="utf-8").count(warning) == 0

    complete_log = workspace_tmp_path / "complete.log"
    with LoggingSession(complete_log):
        run_asr_pipeline(audio_path, workspace, ZeroDurationProvider(), FakePolicy())

    assert warning not in complete_log.read_text(encoding="utf-8")


def test_rejected_provider_candidate_is_not_written_to_chunk_cache(
    workspace_tmp_path: Path, fake_audio
) -> None:
    class InvalidProvider(FakeProvider):
        def transcribe_one(self, prepared, samples, layout):
            transcript = super().transcribe_one(prepared, samples, layout)
            return ChunkTranscript(
                transcript.chunk_index,
                transcript.start_sample,
                transcript.end_sample,
                transcript.text,
                (TranscriptWord(transcript.text, -0.1, 0.5, 0.75),),
                transcript.provider_metadata,
                transcript.elapsed_seconds,
            )

    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"

    with pytest.raises(AlignmentContractError, match="invalid provider timestamp"):
        run_asr_pipeline(audio_path, workspace, InvalidProvider(), FakePolicy())

    assert not (workspace / "chunk_results" / "chunk_000.json").exists()


def test_merge_ignores_empty_accepted_chunks(
    workspace_tmp_path: Path, fake_audio
) -> None:
    class EmptyFirstProvider(FakeProvider):
        def transcribe_one(self, prepared, samples, layout):
            if layout.index == 0:
                return ChunkTranscript(
                    layout.index,
                    layout.start_sample,
                    layout.end_sample,
                    "",
                    (),
                    {},
                    0.1,
                )
            return super().transcribe_one(prepared, samples, layout)

    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"

    run_asr_pipeline(audio_path, workspace, EmptyFirstProvider(), FakePolicy())

    result = read_json(workspace / "result.json")
    assert result["text"] == "two"
    assert result["items"][0]["start"] == 1.0


def test_merge_rejects_transcription_when_every_chunk_is_empty(
    workspace_tmp_path: Path, fake_audio
) -> None:
    class EmptyProvider(FakeProvider):
        def transcribe_one(self, _prepared, _samples, layout):
            return ChunkTranscript(
                layout.index,
                layout.start_sample,
                layout.end_sample,
                "",
                (),
                {},
                0.1,
            )

    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"

    with pytest.raises(RuntimeError, match="must contain text and timestamps"):
        run_asr_pipeline(audio_path, workspace, EmptyProvider(), FakePolicy())

    assert not (workspace / "result.json").exists()


def test_decoded_sample_count_change_reruns_vad_and_invalidates_results(
    workspace_tmp_path: Path, fake_audio, monkeypatch
) -> None:
    class FlexiblePolicy(FakePolicy):
        def layouts(self, sample_count, speech, identity):
            del speech, identity
            if sample_count == CHANGED_SAMPLE_COUNT:
                return (
                    ChunkLayout(0, 0, FAKE_SPLIT, "silence", FAKE_SPLIT),
                    ChunkLayout(
                        1,
                        FAKE_SPLIT,
                        CHANGED_SAMPLE_COUNT,
                        "audio_end",
                        CHANGED_SAMPLE_COUNT - FAKE_SPLIT,
                    ),
                )
            return super().layouts(sample_count, [], {})

    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    workspace = workspace_tmp_path / "asr"
    policy = FlexiblePolicy(
        planning_parameters=PlanningParameters(1, CHANGED_SAMPLE_COUNT)
    )
    run_asr_pipeline(audio_path, workspace, FakeProvider(), policy)
    (workspace / "chunk_results" / "chunk_001.json").unlink()
    monkeypatch.setattr(
        "scripts.asr.pipeline.decode_normalized_audio",
        lambda _path: NormalizedAudio(np.zeros(CHANGED_SAMPLE_COUNT, dtype=np.float32)),
    )
    vad_calls = []
    monkeypatch.setattr(
        "scripts.asr.pipeline.detect_speech_samples",
        lambda *_args: vad_calls.append(1) or [(0, CHANGED_SAMPLE_COUNT)],
    )
    provider = FakeProvider()

    run_asr_pipeline(audio_path, workspace, provider, policy)

    assert vad_calls == [1]
    assert provider.transcribed_indices == [0, 1]
    assert (
        read_json(workspace / "asr_plan.json")["source"]["sample_count"]
        == CHANGED_SAMPLE_COUNT
    )


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
    assert not (workspace / "progress.json").exists()
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
    assert not (workspace / "progress.json").exists()
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
    assert f"ASR audio decode: complete samples={FAKE_SAMPLE_COUNT}" in log_text
    assert (
        f"ASR VAD: complete intervals=1 speech_samples={FAKE_SAMPLE_COUNT}" in log_text
    )
    assert "ASR plan: status=created chunks=2" in log_text
    assert "[Transcribe] cache: reused=0, pending=2, total=2" in log_text
    assert "ASR execution: start provider=fake policy=fake pending=2" in log_text
    assert "ASR merge: complete chunks=2 source=execution" in log_text
    assert "ASR pipeline success: provider=fake policy=fake" in log_text
    assert "one" not in log_text
    assert "two" not in log_text
