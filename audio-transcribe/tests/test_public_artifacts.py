from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest

from scripts.alignment import AlignmentItem
from scripts.artifacts import (
    ArtifactContractError,
    publish_result,
    resolve_artifact_path,
    validate_manifest,
    validate_raw_timestamps,
)
from scripts.io_utils import canonical_sha256, read_json, write_json_atomic
from scripts.model_identity import MODEL_REVISIONS
from scripts.process_logging import get_logger
from scripts.transcribe import EngineResult, run_transcribe


def _samples() -> list[float]:
    return [0.0] * 16_000


def _engine_calls(calls: list[int]):
    def transcribe(_samples, request, _execution):
        calls.append(1)
        assert request["provider"] == "faster-whisper"
        return EngineResult(
            "你好。",
            [
                AlignmentItem("你好", 0.0, 0.5, 0.9),
                AlignmentItem("。", 0.5, 0.6, None),
            ],
        )

    return transcribe


def _hold_variant_lock(
    variant_dir: str,
    acquired,
    release,
) -> None:
    from scripts.artifacts import variant_lock

    with variant_lock(Path(variant_dir)):
        acquired.set()
        release.wait(10)


def test_canonical_variant_hash_is_stable_and_rejects_nan() -> None:
    left = {"language": "zh", "nested": {"b": 2, "a": "文本"}}
    right = {"nested": {"a": "文本", "b": 2}, "language": "zh"}

    assert canonical_sha256(left) == canonical_sha256(right)
    assert len(canonical_sha256(left)) == 64
    with pytest.raises(ValueError):
        canonical_sha256({"invalid": float("nan")})


def test_variant_lock_serializes_windows_processes(
    workspace_tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    first_acquired = context.Event()
    first_release = context.Event()
    second_acquired = context.Event()
    second_release = context.Event()
    variant_dir = str(workspace_tmp_path / "variant")
    first = context.Process(
        target=_hold_variant_lock,
        args=(variant_dir, first_acquired, first_release),
    )
    second = context.Process(
        target=_hold_variant_lock,
        args=(variant_dir, second_acquired, second_release),
    )
    try:
        first.start()
        assert first_acquired.wait(5)
        second.start()
        time.sleep(0.25)
        assert not second_acquired.is_set()
        first_release.set()
        assert second_acquired.wait(5)
        second_release.set()
    finally:
        first_release.set()
        second_release.set()
        first.join(5)
        second.join(5)
        if first.is_alive():
            first.terminate()
        if second.is_alive():
            second.terminate()
    assert first.exitcode == 0
    assert second.exitcode == 0


def test_model_revisions_are_full_pinned_commits() -> None:
    assert MODEL_REVISIONS["faster-whisper"]["revision"] == (
        "536b0662742c02347bc0e980a01041f333bce120"
    )
    assert MODEL_REVISIONS["qwen3"]["revision"] == (
        "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
    )
    assert MODEL_REVISIONS["qwen3"]["aligner_revision"] == (
        "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
    )


def test_content_identity_reuses_result_after_input_rename(
    workspace_tmp_path: Path,
) -> None:
    first = workspace_tmp_path / "first.audio"
    second = workspace_tmp_path / "renamed.audio"
    first.write_bytes(b"same audio bytes")
    calls: list[int] = []
    results = workspace_tmp_path / "results"

    first_manifest = run_transcribe(
        first,
        language="zh",
        provider="faster-whisper",
        results_dir=results,
        decoder=lambda _path: _samples(),
        engine=_engine_calls(calls),
    )
    first.rename(second)
    second_manifest = run_transcribe(
        second,
        language="zh",
        provider="faster-whisper",
        results_dir=results,
        decoder=lambda _path: _samples(),
        engine=_engine_calls(calls),
    )

    assert first_manifest == second_manifest
    assert calls == [1]
    manifest = validate_manifest(first_manifest)
    assert manifest["request"]["provider"] == "faster-whisper"
    assert len(manifest["request"]["variant_id"]) == 64
    assert first_manifest.parent.name.endswith(manifest["request"]["variant_id"])
    transcript = read_json(first_manifest.parent / "transcript.json")
    assert transcript["segments"] == [
        {"end": 0.6, "id": 0, "start": 0.0, "text": "你好。"}
    ]
    assert "first.audio" not in json.dumps(manifest, ensure_ascii=False)


def test_first_success_log_is_complete_and_cache_calls_do_not_modify_it(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    results = workspace_tmp_path / "results"

    def engine(_samples, _request, _execution):
        get_logger("test").info("engine completed")
        return EngineResult(
            "你好。",
            [AlignmentItem("你好。", 0.0, 0.5, None)],
        )

    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "results_dir": results,
        "decoder": lambda _path: _samples(),
        "engine": engine,
    }
    manifest_path = run_transcribe(audio, **kwargs)
    log_path = manifest_path.parent / "transcribe.log"
    original_log = log_path.read_bytes()

    assert b"Transcription invocation:" in original_log
    assert b"engine completed" in original_log
    assert run_transcribe(audio, **kwargs) == manifest_path
    assert log_path.read_bytes() == original_log


def test_failed_attempt_log_is_replaced_by_first_success(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    results = workspace_tmp_path / "results"

    def fail(_samples, _request, _execution):
        get_logger("test").info("failed attempt marker")
        raise RuntimeError("expected failure")

    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "results_dir": results,
        "decoder": lambda _path: _samples(),
    }
    with pytest.raises(RuntimeError, match="expected failure"):
        run_transcribe(audio, engine=fail, **kwargs)

    manifest_path = run_transcribe(audio, engine=_engine_calls([]), **kwargs)
    log_text = (manifest_path.parent / "transcribe.log").read_text(encoding="utf-8")
    assert "failed attempt marker" not in log_text
    assert "Transcription invocation:" in log_text


def test_malformed_manifest_is_hidden_before_recovery(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "results_dir": workspace_tmp_path / "results",
        "decoder": lambda _path: _samples(),
        "engine": _engine_calls([]),
    }
    manifest_path = run_transcribe(audio, **kwargs)
    manifest_path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError):
        run_transcribe(audio, **kwargs)

    assert not manifest_path.exists()


def test_qwen_probability_and_overlapping_timestamps_are_rejected() -> None:
    common = {
        "schema_version": 1,
        "audio_id": "a" * 64,
        "variant_id": "b" * 64,
        "provider": "qwen3",
        "language": "zh",
        "duration": 1.0,
    }
    with pytest.raises(ArtifactContractError, match="probability"):
        validate_raw_timestamps(
            {
                **common,
                "items": [
                    {
                        "text": "词",
                        "start": 0.0,
                        "end": 0.5,
                        "probability": 0.9,
                    }
                ],
            },
            audio_id=common["audio_id"],
            variant_id=common["variant_id"],
        )
    with pytest.raises(ArtifactContractError, match="monotonic"):
        validate_raw_timestamps(
            {
                **common,
                "items": [
                    {"text": "前", "start": 0.0, "end": 0.5, "probability": None},
                    {
                        "text": "后",
                        "start": 0.4995,
                        "end": 0.8,
                        "probability": None,
                    },
                ],
            },
            audio_id=common["audio_id"],
            variant_id=common["variant_id"],
        )


def test_preprocessing_decodes_and_runs_vad_once_before_language_resolution(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    calls = {"decode": 0, "vad": 0, "language": 0}

    def decode(_path: Path):
        calls["decode"] += 1
        return [0.0] * 16_000

    def vad(normalized):
        calls["vad"] += 1
        assert normalized.sample_count == 16_000
        return [(100, 300)]

    def detect(samples):
        calls["language"] += 1
        assert len(samples) == 200
        return "zh"

    run_transcribe(
        audio,
        provider="faster-whisper",
        results_dir=workspace_tmp_path / "results",
        decoder=decode,
        vad_detector=vad,
        language_detector=detect,
        engine=_engine_calls([]),
    )

    assert calls == {"decode": 1, "vad": 1, "language": 1}


def test_missing_public_artifact_is_rebuilt_without_inference(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    calls: list[int] = []
    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "results_dir": workspace_tmp_path / "results",
        "decoder": lambda _path: _samples(),
        "engine": _engine_calls(calls),
    }
    manifest_path = run_transcribe(audio, **kwargs)
    original_manifest = manifest_path.read_bytes()
    (manifest_path.parent / "transcript.json").unlink()

    assert run_transcribe(audio, **kwargs) == manifest_path
    assert calls == [1]
    assert manifest_path.read_bytes() == original_manifest
    validate_manifest(manifest_path)


def test_migrated_pipeline_workspace_is_adapted_to_public_schema(
    workspace_tmp_path: Path,
) -> None:
    variant_dir = workspace_tmp_path / "variant"
    workspace_path = variant_dir / "workspace" / "result.json"
    audio_id = "a" * 64
    canonical_request = {
        "provider": "faster-whisper",
        "language": "zh",
    }
    variant_id = canonical_sha256(canonical_request)
    write_json_atomic(
        workspace_path,
        {
            "schema_version": 1,
            "plan": {
                "source": {"sample_count": 16_000, "sample_rate": 16_000},
                "provider_request": {
                    "provider": "faster-whisper",
                    "language": "zh",
                },
            },
            "text": "原文。",
            "words": [
                {"text": "原文", "start": 0.0, "end": 0.5, "probability": 0.8},
                {"text": "。", "start": 0.5, "end": 0.6, "probability": None},
            ],
            "segments": [],
        },
    )
    (variant_dir / "transcribe.log").write_text("test\n", encoding="utf-8")

    manifest_path = publish_result(
        variant_dir,
        audio={
            "id": audio_id,
            "size": 10,
            "sample_count": 16_000,
            "sample_rate": 16_000,
            "duration": 1.0,
        },
        request={"variant_id": variant_id, **canonical_request},
    )

    validate_manifest(manifest_path)
    transcript = read_json(variant_dir / "transcript.json")
    assert transcript["segments"][0]["text"] == "原文。"


def test_recovery_refuses_workspace_that_changes_published_digest(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "results_dir": workspace_tmp_path / "results",
        "decoder": lambda _path: _samples(),
        "engine": _engine_calls([]),
    }
    manifest_path = run_transcribe(audio, **kwargs)
    workspace_path = manifest_path.parent / "workspace" / "result.json"
    workspace = read_json(workspace_path)
    workspace["text"] = "再见。"
    workspace["items"] = [
        {"text": "再见", "start": 0.0, "end": 0.5, "probability": None},
        {"text": "。", "start": 0.5, "end": 0.6, "probability": None},
    ]
    write_json_atomic(workspace_path, workspace)
    (manifest_path.parent / "transcript.json").unlink()

    with pytest.raises(ArtifactContractError, match="does not match"):
        run_transcribe(audio, **kwargs)

    assert not manifest_path.exists()


def test_empty_result_never_publishes_complete_manifest(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    results = workspace_tmp_path / "results"

    with pytest.raises(ArtifactContractError, match="must contain"):
        run_transcribe(
            audio,
            language="en",
            provider="faster-whisper",
            results_dir=results,
            decoder=lambda _path: _samples(),
            engine=lambda *_args: EngineResult("", []),
        )

    assert not list(results.rglob("result_manifest.json"))


@pytest.mark.parametrize("unsafe", ["../outside.json", "/absolute.json"])
def test_manifest_artifact_path_cannot_escape_result_directory(
    workspace_tmp_path: Path, unsafe: str
) -> None:
    manifest_path = workspace_tmp_path / "result" / "result_manifest.json"

    with pytest.raises(ArtifactContractError):
        resolve_artifact_path(manifest_path, unsafe)
