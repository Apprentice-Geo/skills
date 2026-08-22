from __future__ import annotations

import errno
import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest
from audio_transcribe_contract import ResultValidationError, load_result

from scripts.artifacts import (
    publish_result,
    recover_public_artifacts,
    variant_lock,
    write_workspace_result,
)
from scripts.asr.alignment import ALIGNMENT_POLICY, AlignedTranscript, AlignmentItem
from scripts.io_utils import canonical_sha256, read_json, write_json_atomic
from scripts.model_identity import MODEL_REVISIONS
from scripts.process_logging import get_logger
from scripts.transcribe import run_transcribe


def _samples() -> list[float]:
    return [0.0] * 16_000


def _engine_calls(calls: list[int]):
    def transcribe(_samples, request, _execution):
        calls.append(1)
        assert request["provider"] == "faster-whisper"
        return AlignedTranscript(
            "你好。",
            (
                AlignmentItem("你好", 0.0, 0.5, 0.9),
                AlignmentItem("。", 0.5, 0.6, None),
            ),
        )

    return transcribe


def _publication_inputs(
    variant_dir: Path,
    *,
    provider: str = "faster-whisper",
) -> tuple[dict[str, object], dict[str, object]]:
    canonical_request = {
        "provider": provider,
        "language": "en",
        "alignment_policy": dict(ALIGNMENT_POLICY),
    }
    variant_id = canonical_sha256(canonical_request)
    write_workspace_result(
        variant_dir / "workspace" / "result.json",
        text="ok",
        items=[AlignmentItem("ok", 0.0, 0.5, None)],
        duration=1.0,
        provider=provider,
        language="en",
    )
    (variant_dir / "transcribe.log").write_text("test\n", encoding="utf-8")
    return (
        {
            "id": "a" * 64,
            "size": 10,
            "sample_count": 16_000,
            "sample_rate": 16_000,
            "duration": 1.0,
        },
        {"variant_id": variant_id, **canonical_request},
    )


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


def test_variant_lock_does_not_unlock_after_acquisition_failure(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquisition_error = OSError(errno.EDEADLK, "lock acquisition failed")
    calls: list[int] = []
    if os.name == "nt":
        import msvcrt

        lock_module = msvcrt
        lock_mode = msvcrt.LK_LOCK
        lock_function = "locking"
    else:
        import fcntl

        lock_module = fcntl
        lock_mode = fcntl.LOCK_EX
        lock_function = "flock"

    def fail_lock(_fd: int, mode: int, *_args: int) -> None:
        calls.append(mode)
        if mode == lock_mode:
            raise acquisition_error
        raise PermissionError("attempted to release an unowned lock")

    monkeypatch.setattr(lock_module, lock_function, fail_lock)

    with pytest.raises(OSError) as caught:
        with variant_lock(workspace_tmp_path / "variant"):
            pass

    assert caught.value is acquisition_error
    assert calls == [lock_mode]


def test_model_revisions_are_full_pinned_commits() -> None:
    assert MODEL_REVISIONS["faster-whisper"]["revision"] == (
        "536b0662742c02347bc0e980a01041f333bce120"
    )
    assert MODEL_REVISIONS["qwen3-asr"]["revision"] == (
        "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"
    )
    assert MODEL_REVISIONS["qwen3-asr"]["aligner_revision"] == (
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
    manifest = load_result(first_manifest).manifest
    assert manifest["request"]["provider"] == "faster-whisper"
    assert manifest["request"]["text_normalization"] == {
        "schema_version": 1,
        "unicode_normalization": "NFKC",
        "zh_conversion": "OpenCC t2s",
    }
    assert manifest["request"]["alignment_policy"] == {
        "schema_version": 1,
        "timestamp_resolution_ms": 1,
        "zero_duration": "drop_item_and_owned_text",
        "ordering": "strict",
    }
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
        return AlignedTranscript(
            "你好。",
            (AlignmentItem("你好。", 0.0, 0.5, None),),
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

    assert manifest_path.is_file()


def test_quantized_segment_end_recovers_identically(
    workspace_tmp_path: Path,
) -> None:
    variant_dir = workspace_tmp_path / "variant"
    workspace_path = variant_dir / "workspace" / "result.json"
    duration = 1.0004
    audio_id = "a" * 64
    canonical_request = {
        "provider": "faster-whisper",
        "language": "zh",
        "alignment_policy": dict(ALIGNMENT_POLICY),
    }
    variant_id = canonical_sha256(canonical_request)
    write_workspace_result(
        workspace_path,
        text="末",
        items=[AlignmentItem("末", 0.0, 1.0, None)],
        duration=duration,
        provider="faster-whisper",
        language="zh",
    )
    (variant_dir / "transcribe.log").write_text("test\n", encoding="utf-8")

    manifest_path = publish_result(
        variant_dir,
        audio={
            "id": audio_id,
            "size": 10,
            "sample_count": 10_004,
            "sample_rate": 10_000,
            "duration": duration,
        },
        request={"variant_id": variant_id, **canonical_request},
    )
    transcript_path = variant_dir / "transcript.json"
    original_manifest = manifest_path.read_bytes()
    original_transcript = transcript_path.read_bytes()
    manifest = read_json(manifest_path)

    assert read_json(transcript_path)["segments"][0]["end"] == 1.0
    assert read_json(variant_dir / "raw_timestamps.json")["items"][0]["end"] == 1.0

    transcript_path.unlink()
    recover_public_artifacts(manifest_path)

    assert transcript_path.read_bytes() == original_transcript
    assert manifest_path.read_bytes() == original_manifest
    assert read_json(manifest_path)["artifact_sha256"] == manifest["artifact_sha256"]


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
    load_result(manifest_path)


def test_workspace_without_current_fields_is_rejected(
    workspace_tmp_path: Path,
) -> None:
    variant_dir = workspace_tmp_path / "variant"
    workspace_path = variant_dir / "workspace" / "result.json"
    audio_id = "a" * 64
    canonical_request = {
        "provider": "faster-whisper",
        "language": "zh",
        "alignment_policy": dict(ALIGNMENT_POLICY),
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

    with pytest.raises(ResultValidationError, match="Invalid workspace result"):
        publish_result(
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


@pytest.mark.parametrize(
    "mutate",
    [
        lambda workspace: workspace.__setitem__("extra", True),
        lambda workspace: workspace.__setitem__("duration", "1.0"),
        lambda workspace: workspace.__setitem__("duration", 2.0),
        lambda workspace: workspace.__setitem__("provider", 1),
        lambda workspace: workspace.__setitem__("provider", "qwen3-asr"),
        lambda workspace: workspace.__setitem__("language", []),
        lambda workspace: workspace.__setitem__("language", "zh"),
        lambda workspace: workspace["items"][0].__setitem__("extra", True),
        lambda workspace: workspace["items"][0].__setitem__("start", "0.0"),
        lambda workspace: workspace["items"][0].__setitem__("end", 0.0),
        lambda workspace: workspace["items"][0].__setitem__("probability", True),
    ],
)
def test_publication_rejects_corrupt_workspace_shape_and_types(
    workspace_tmp_path: Path,
    mutate,
) -> None:
    variant_dir = workspace_tmp_path / "variant"
    audio, request = _publication_inputs(variant_dir)
    workspace_path = variant_dir / "workspace" / "result.json"
    workspace = read_json(workspace_path)
    mutate(workspace)
    write_json_atomic(workspace_path, workspace)

    with pytest.raises(ResultValidationError, match="Invalid workspace result"):
        publish_result(variant_dir, audio=audio, request=request)

    assert not (variant_dir / "result_manifest.json").exists()


def test_publication_rejects_qwen_probability_in_workspace(
    workspace_tmp_path: Path,
) -> None:
    variant_dir = workspace_tmp_path / "variant"
    audio, request = _publication_inputs(variant_dir, provider="qwen3-asr")
    workspace_path = variant_dir / "workspace" / "result.json"
    workspace = read_json(workspace_path)
    workspace["items"][0]["probability"] = 0.9
    write_json_atomic(workspace_path, workspace)

    with pytest.raises(ResultValidationError, match="Invalid workspace result"):
        publish_result(variant_dir, audio=audio, request=request)


def test_candidate_validation_failure_never_publishes_manifest(
    workspace_tmp_path: Path,
) -> None:
    variant_dir = workspace_tmp_path / "variant"
    audio, request = _publication_inputs(variant_dir)
    request.pop("alignment_policy")
    request["variant_id"] = canonical_sha256(
        {key: value for key, value in request.items() if key != "variant_id"}
    )

    with pytest.raises(ResultValidationError, match="alignment_policy"):
        publish_result(variant_dir, audio=audio, request=request)

    assert not (variant_dir / "result_manifest.json").exists()
    assert not (variant_dir / ".result_manifest.json.incomplete").exists()


def test_existing_complete_manifest_is_never_overwritten(
    workspace_tmp_path: Path,
) -> None:
    variant_dir = workspace_tmp_path / "variant"
    audio, request = _publication_inputs(variant_dir)
    manifest_path = publish_result(variant_dir, audio=audio, request=request)
    before = {
        path.name: path.read_bytes() for path in variant_dir.iterdir() if path.is_file()
    }

    with pytest.raises(ResultValidationError, match="already exists"):
        publish_result(variant_dir, audio=audio, request=request)

    assert {
        path.name: path.read_bytes() for path in variant_dir.iterdir() if path.is_file()
    } == before
    load_result(manifest_path)


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

    with pytest.raises(ResultValidationError, match="does not match"):
        run_transcribe(audio, **kwargs)

    assert manifest_path.is_file()


def test_empty_result_never_publishes_complete_manifest(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    results = workspace_tmp_path / "results"

    with pytest.raises(ResultValidationError, match="must contain"):
        run_transcribe(
            audio,
            language="en",
            provider="faster-whisper",
            results_dir=results,
            decoder=lambda _path: _samples(),
            engine=lambda *_args: AlignedTranscript("", ()),
        )

    assert not list(results.rglob("result_manifest.json"))


def test_fake_engine_uses_provider_acceptance_boundary(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")

    manifest_path = run_transcribe(
        audio,
        language="en",
        provider="faster-whisper",
        results_dir=workspace_tmp_path / "results",
        decoder=lambda _path: _samples(),
        engine=lambda *_args: AlignedTranscript(
            "echo echo",
            (
                AlignmentItem("echo", 0.1001, 0.1004, 0.9),
                AlignmentItem("echo", 0.1004, 0.5, 0.8),
            ),
        ),
    )

    result = load_result(manifest_path)
    assert [item["text"] for item in result.raw_timestamps["items"]] == ["echo"]
    log_text = (manifest_path.parent / "transcribe.log").read_text(encoding="utf-8")
    assert log_text.count("action=drop_zero_duration_items dropped=1") == 1


def test_workspace_normalizes_public_text_and_items(workspace_tmp_path: Path) -> None:
    workspace_path = workspace_tmp_path / "result.json"

    write_workspace_result(
        workspace_path,
        text="Ａ臺灣。",
        items=[AlignmentItem("Ａ臺灣。", 0.0, 0.5, None)],
        duration=1.0,
        provider="faster-whisper",
        language="zh",
    )

    workspace = read_json(workspace_path)
    assert workspace["text"] == "A台湾。"
    assert workspace["items"][0]["text"] == "A台湾。"


def test_workspace_projects_phrase_normalization_onto_items(
    workspace_tmp_path: Path,
) -> None:
    workspace_path = workspace_tmp_path / "result.json"

    write_workspace_result(
        workspace_path,
        text="彷彿",
        items=[
            AlignmentItem("彷", 0.0, 0.2, 0.9),
            AlignmentItem("彿", 0.2, 0.5, 0.8),
        ],
        duration=1.0,
        provider="faster-whisper",
        language="zh",
    )

    assert read_json(workspace_path)["items"] == [
        {"text": "仿", "start": 0.0, "end": 0.2, "probability": 0.9},
        {"text": "佛", "start": 0.2, "end": 0.5, "probability": 0.8},
    ]


def test_workspace_merges_items_for_length_changing_normalization(
    workspace_tmp_path: Path,
) -> None:
    workspace_path = workspace_tmp_path / "result.json"

    write_workspace_result(
        workspace_path,
        text="㍿",
        items=[AlignmentItem("㍿", 0.1, 0.6, 0.7)],
        duration=1.0,
        provider="faster-whisper",
        language="en",
    )

    workspace = read_json(workspace_path)
    assert workspace["text"] == "株式会社"
    assert workspace["items"] == [
        {"text": "株式会社", "start": 0.1, "end": 0.6, "probability": 0.7}
    ]


def test_non_zh_workspace_only_applies_nfkc(workspace_tmp_path: Path) -> None:
    workspace_path = workspace_tmp_path / "result.json"

    write_workspace_result(
        workspace_path,
        text="Ａ臺灣",
        items=[AlignmentItem("Ａ臺灣", 0.0, 0.5, None)],
        duration=1.0,
        provider="faster-whisper",
        language="yue",
    )

    assert read_json(workspace_path)["text"] == "A臺灣"
