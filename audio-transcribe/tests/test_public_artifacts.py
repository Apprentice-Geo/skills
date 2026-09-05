from __future__ import annotations

import errno
import json
import multiprocessing
import os
import shutil
import time
from pathlib import Path

import pytest
from audio_transcribe_contract import ResultValidationError, load_result

from scripts.artifacts import (
    publish_result,
    result_lock,
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
    result_dir: Path,
    *,
    provider: str = "faster-whisper",
) -> tuple[dict[str, object], dict[str, object]]:
    canonical_request = {
        "provider": provider,
        "language": "en",
        "public_schema_version": 2,
        "alignment_policy": dict(ALIGNMENT_POLICY),
    }
    audio_id = "a" * 64
    config_digest = canonical_sha256(canonical_request)
    write_workspace_result(
        result_dir / "workspace" / "result.json",
        audio_id=audio_id,
        config_digest=config_digest,
        text="ok",
        items=[AlignmentItem("ok", 0.0, 0.5, None)],
        duration=1.0,
        provider=provider,
        language="en",
    )
    (result_dir / "transcribe.log").write_text("test\n", encoding="utf-8")
    return (
        {
            "id": audio_id,
            "size": 10,
            "sample_count": 16_000,
            "sample_rate": 16_000,
            "duration": 1.0,
        },
        {"config_digest": config_digest, **canonical_request},
    )


def _hold_result_lock(
    result_dir: str,
    acquired,
    release,
) -> None:
    from scripts.artifacts import result_lock

    with result_lock(Path(result_dir)):
        acquired.set()
        release.wait(10)


def test_canonical_config_digest_is_stable_and_rejects_nan() -> None:
    left = {"language": "zh", "nested": {"b": 2, "a": "文本"}}
    right = {"nested": {"a": "文本", "b": 2}, "language": "zh"}

    assert canonical_sha256(left) == canonical_sha256(right)
    assert len(canonical_sha256(left)) == 64
    with pytest.raises(ValueError):
        canonical_sha256({"invalid": float("nan")})


def test_result_lock_serializes_windows_processes(
    workspace_tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    first_acquired = context.Event()
    first_release = context.Event()
    second_acquired = context.Event()
    second_release = context.Event()
    result_dir = str(workspace_tmp_path / "result")
    first = context.Process(
        target=_hold_result_lock,
        args=(result_dir, first_acquired, first_release),
    )
    second = context.Process(
        target=_hold_result_lock,
        args=(result_dir, second_acquired, second_release),
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


def test_result_lock_does_not_unlock_after_acquisition_failure(
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
        with result_lock(workspace_tmp_path / "result"):
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
    ).manifest_path
    first.rename(second)
    second_manifest = run_transcribe(
        second,
        language="zh",
        provider="faster-whisper",
        results_dir=results,
        decoder=lambda _path: _samples(),
        engine=_engine_calls(calls),
    ).manifest_path

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
    assert len(manifest["request"]["config_digest"]) == 64
    assert first_manifest.parent.name.endswith(manifest["request"]["config_digest"])
    transcript = read_json(first_manifest.parent / "transcript.json")
    assert transcript["segments"] == [
        {"end": 0.6, "id": 0, "start": 0.0, "text": "你好。"}
    ]
    assert "first.audio" not in json.dumps(manifest, ensure_ascii=False)
    request = manifest["request"]
    assert request["public_schema_version"] == 2
    assert (
        canonical_sha256(
            {key: value for key, value in request.items() if key != "config_digest"}
        )
        == request["config_digest"]
    )
    old_format = {
        key: value
        for key, value in request.items()
        if key not in {"config_digest", "public_schema_version"}
    }
    assert canonical_sha256(old_format) != request["config_digest"]


def test_production_reuses_bundle_without_private_state(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    calls: list[int] = []
    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "decoder": lambda _path: _samples(),
        "engine": _engine_calls(calls),
    }
    original_root = workspace_tmp_path / "original"
    source = run_transcribe(audio, results_dir=original_root, **kwargs).manifest_path
    copy_root = workspace_tmp_path / "copied"
    destination = copy_root / source.relative_to(original_root)
    destination.parent.mkdir(parents=True)
    for name in ("manifest.json", "transcript.json"):
        shutil.copy2(source.parent / name, destination.parent / name)
    assert (
        run_transcribe(audio, results_dir=copy_root, **kwargs).manifest_path
        == destination
    )
    assert calls == [1]
    assert not (destination.parent / "workspace").exists()
    assert not (destination.parent / "transcribe.log").exists()


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
    manifest_path = run_transcribe(audio, **kwargs).manifest_path
    log_path = manifest_path.parent / "transcribe.log"
    original_log = log_path.read_bytes()

    assert b"Transcription invocation:" in original_log
    assert b"engine completed" in original_log
    cached = run_transcribe(audio, **kwargs)
    assert cached.manifest_path == manifest_path
    assert cached.pipeline_outcome is None
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

    manifest_path = run_transcribe(
        audio, engine=_engine_calls([]), **kwargs
    ).manifest_path
    log_text = (manifest_path.parent / "transcribe.log").read_text(encoding="utf-8")
    assert "failed attempt marker" not in log_text
    assert "Transcription invocation:" in log_text


def test_malformed_manifest_is_republished_from_workspace(
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
    manifest_path = run_transcribe(audio, **kwargs).manifest_path
    manifest_path.write_text("{", encoding="utf-8")

    run_transcribe(audio, **kwargs)

    load_result(manifest_path)
    assert calls == [1]


@pytest.mark.parametrize(
    ("manifest_state", "changed_chunk"),
    [
        ("valid", False),
        ("damaged", False),
        ("missing", False),
        ("valid", True),
        ("damaged", True),
    ],
)
def test_production_recovers_from_chunks_without_model(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_state: str,
    changed_chunk: bool,
) -> None:
    from scripts.asr.pipeline_types import ChunkTranscript
    from scripts.asr.providers import WhisperProvider

    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(WhisperProvider, "prepare", lambda *_: object())

    def transcribe(_self, _model, _samples, layout):
        return ChunkTranscript(
            layout.index,
            layout.start_sample,
            layout.end_sample,
            "你好。",
            (AlignmentItem("你好。", 0.0, 0.5, 0.9),),
            {},
            0.0,
        )

    monkeypatch.setattr(WhisperProvider, "transcribe_one", transcribe)
    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "results_dir": workspace_tmp_path / "results",
        "decoder": lambda _path: _samples(),
        "vad_detector": lambda _audio: [(0, 16_000)],
    }
    manifest_path = run_transcribe(audio, **kwargs).manifest_path
    original_manifest = manifest_path.read_bytes()
    body = manifest_path.parent / "transcript.json"
    original_body = body.read_bytes()
    workspace = manifest_path.parent / "workspace"
    (workspace / "result.json").write_text("{", encoding="utf-8")
    (workspace / "vad_result.json").write_text("{", encoding="utf-8")
    body.write_text("damaged body", encoding="utf-8")
    if changed_chunk:
        chunk_path = workspace / "chunk_results" / "chunk_000.json"
        chunk = read_json(chunk_path)
        chunk["text"] = "再见。"
        chunk["items"][0]["text"] = "再见。"
        write_json_atomic(chunk_path, chunk)
    if manifest_state == "damaged":
        manifest_path.write_text("{", encoding="utf-8")
    elif manifest_state == "missing":
        manifest_path.unlink()
    monkeypatch.setattr(
        WhisperProvider,
        "prepare",
        lambda *_: pytest.fail("model loaded for chunk recovery"),
    )
    monkeypatch.setattr(
        WhisperProvider,
        "transcribe_one",
        lambda *_: pytest.fail("inference ran for chunk recovery"),
    )
    if manifest_state == "valid" and changed_chunk:
        with pytest.raises(ResultValidationError, match="does not match"):
            run_transcribe(audio, **kwargs)
        assert manifest_path.read_bytes() == original_manifest
        assert body.read_bytes() == b"damaged body"
    else:
        run_transcribe(audio, **kwargs)
        result = load_result(manifest_path)
        if changed_chunk:
            assert result.transcript["segments"][0]["text"] == "再见。"
            assert manifest_path.read_bytes() != original_manifest
        else:
            assert body.read_bytes() == original_body
            assert manifest_path.read_bytes() == original_manifest


@pytest.mark.parametrize("manifest_valid", [True, False])
def test_missing_cache_falls_back_to_inference(
    workspace_tmp_path: Path, manifest_valid: bool
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
    path = run_transcribe(audio, **kwargs).manifest_path
    original = path.read_bytes()
    (path.parent / "workspace" / "result.json").unlink()
    (path.parent / "transcript.json").unlink()
    if not manifest_valid:
        path.write_text("{", encoding="utf-8")
    run_transcribe(audio, **kwargs)
    assert calls == [1, 1]
    assert path.read_bytes() == original
    load_result(path)


@pytest.mark.parametrize("phase", ["validation", "manifest_install"])
@pytest.mark.parametrize("manifest_valid", [True, False])
def test_publication_failure_preserves_previous_files(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    manifest_valid: bool,
) -> None:
    result_dir = workspace_tmp_path / "result"
    audio, request = _publication_inputs(result_dir)
    manifest_path = publish_result(result_dir, audio=audio, request=request)
    body = result_dir / "transcript.json"
    body.write_text("damaged body", encoding="utf-8")
    if not manifest_valid:
        manifest_path.write_text("{", encoding="utf-8")
    before = {path: path.read_bytes() for path in (manifest_path, body)}
    if phase == "validation":

        def validate(path):
            if path.parent.name.startswith(".publication-"):
                raise ResultValidationError("candidate rejected")
            return load_result(path)

        monkeypatch.setattr("scripts.artifacts.load_result", validate)
    else:
        real_replace = os.replace

        def replace(source, destination):
            if Path(destination) == manifest_path:
                raise OSError("manifest install failed")
            return real_replace(source, destination)

        monkeypatch.setattr("scripts.artifacts.os.replace", replace)
    with pytest.raises((ResultValidationError, OSError)):
        publish_result(result_dir, audio=audio, request=request, replace_existing=True)
    assert {path: path.read_bytes() for path in before} == before
    assert not list(result_dir.glob(".publication-*"))


def test_valid_manifest_for_other_audio_is_not_republished(
    workspace_tmp_path: Path,
) -> None:
    audio_path = workspace_tmp_path / "audio.bin"
    audio_path.write_bytes(b"audio")
    kwargs = {
        "language": "zh",
        "provider": "faster-whisper",
        "results_dir": workspace_tmp_path / "results",
        "decoder": lambda _path: _samples(),
        "engine": _engine_calls([]),
    }
    path = run_transcribe(audio_path, **kwargs).manifest_path
    manifest = read_json(path)
    manifest["audio"]["id"] = "f" * 64
    write_json_atomic(path, manifest)
    before = path.read_bytes()
    with pytest.raises(ResultValidationError, match="identity"):
        run_transcribe(audio_path, **kwargs)
    assert path.read_bytes() == before


def test_quantized_segment_end_recovers_identically(
    workspace_tmp_path: Path,
) -> None:
    result_dir = workspace_tmp_path / "result"
    workspace_path = result_dir / "workspace" / "result.json"
    duration = 1.0004
    audio_id = "a" * 64
    canonical_request = {
        "provider": "faster-whisper",
        "language": "zh",
        "public_schema_version": 2,
        "alignment_policy": dict(ALIGNMENT_POLICY),
    }
    config_digest = canonical_sha256(canonical_request)
    write_workspace_result(
        workspace_path,
        audio_id=audio_id,
        config_digest=config_digest,
        text="末",
        items=[AlignmentItem("末", 0.0, 1.0, None)],
        duration=duration,
        provider="faster-whisper",
        language="zh",
    )
    (result_dir / "transcribe.log").write_text("test\n", encoding="utf-8")

    manifest_path = publish_result(
        result_dir,
        audio={
            "id": audio_id,
            "size": 10,
            "sample_count": 10_004,
            "sample_rate": 10_000,
            "duration": duration,
        },
        request={"config_digest": config_digest, **canonical_request},
    )
    transcript_path = result_dir / "transcript.json"
    original_manifest = manifest_path.read_bytes()
    original_transcript = transcript_path.read_bytes()
    manifest = read_json(manifest_path)

    assert read_json(transcript_path)["segments"][0]["end"] == 1.0
    assert read_json(result_dir / "transcript.json")["items"][0]["end"] == 1.0

    transcript_path.unlink()
    publish_result(
        manifest_path.parent,
        audio=manifest["audio"],
        request=manifest["request"],
        replace_existing=True,
    )

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


def test_specified_language_does_not_run_vad_for_engine(
    workspace_tmp_path: Path,
) -> None:
    audio = workspace_tmp_path / "audio.bin"
    audio.write_bytes(b"audio")

    run_transcribe(
        audio,
        language="zh",
        provider="faster-whisper",
        results_dir=workspace_tmp_path / "results",
        decoder=lambda _path: _samples(),
        vad_detector=lambda _audio: pytest.fail("VAD ran for a specified language"),
        engine=_engine_calls([]),
    )


def test_invalid_workspace_without_manifest_is_rebuilt(
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
    first = run_transcribe(audio, **kwargs).manifest_path
    workspace_path = first.parent / "workspace" / "result.json"
    workspace_path.write_text("{", encoding="utf-8")
    first.unlink()
    (first.parent / "transcript.json").unlink()

    rebuilt = run_transcribe(audio, **kwargs).manifest_path

    assert rebuilt == first
    assert calls == [1, 1]
    load_result(rebuilt)


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
    manifest_path = run_transcribe(audio, **kwargs).manifest_path
    original_manifest = manifest_path.read_bytes()
    (manifest_path.parent / "transcript.json").unlink()

    assert run_transcribe(audio, **kwargs).manifest_path == manifest_path
    assert calls == [1]
    assert manifest_path.read_bytes() == original_manifest
    load_result(manifest_path)


def test_workspace_without_current_fields_is_rejected(
    workspace_tmp_path: Path,
) -> None:
    result_dir = workspace_tmp_path / "result"
    workspace_path = result_dir / "workspace" / "result.json"
    audio_id = "a" * 64
    canonical_request = {
        "provider": "faster-whisper",
        "language": "zh",
        "public_schema_version": 2,
        "alignment_policy": dict(ALIGNMENT_POLICY),
    }
    config_digest = canonical_sha256(canonical_request)
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
    (result_dir / "transcribe.log").write_text("test\n", encoding="utf-8")

    with pytest.raises(ResultValidationError, match="Invalid workspace result"):
        publish_result(
            result_dir,
            audio={
                "id": audio_id,
                "size": 10,
                "sample_count": 16_000,
                "sample_rate": 16_000,
                "duration": 1.0,
            },
            request={"config_digest": config_digest, **canonical_request},
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda workspace: workspace.__setitem__("extra", True),
        lambda workspace: workspace.__setitem__("audio_id", "b" * 64),
        lambda workspace: workspace.__setitem__("config_digest", "c" * 64),
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
    result_dir = workspace_tmp_path / "result"
    audio, request = _publication_inputs(result_dir)
    workspace_path = result_dir / "workspace" / "result.json"
    workspace = read_json(workspace_path)
    mutate(workspace)
    write_json_atomic(workspace_path, workspace)

    with pytest.raises(ResultValidationError, match="Invalid workspace result"):
        publish_result(result_dir, audio=audio, request=request)

    assert not (result_dir / "manifest.json").exists()


def test_publication_rejects_qwen_probability_in_workspace(
    workspace_tmp_path: Path,
) -> None:
    result_dir = workspace_tmp_path / "result"
    audio, request = _publication_inputs(result_dir, provider="qwen3-asr")
    workspace_path = result_dir / "workspace" / "result.json"
    workspace = read_json(workspace_path)
    workspace["items"][0]["probability"] = 0.9
    write_json_atomic(workspace_path, workspace)

    with pytest.raises(ResultValidationError, match="Invalid workspace result"):
        publish_result(result_dir, audio=audio, request=request)


def test_candidate_validation_failure_never_publishes_manifest(
    workspace_tmp_path: Path,
) -> None:
    result_dir = workspace_tmp_path / "result"
    audio, request = _publication_inputs(result_dir)
    request.pop("alignment_policy")
    request["config_digest"] = canonical_sha256(
        {key: value for key, value in request.items() if key != "config_digest"}
    )
    workspace_path = result_dir / "workspace" / "result.json"
    workspace = read_json(workspace_path)
    workspace["config_digest"] = request["config_digest"]
    write_json_atomic(workspace_path, workspace)

    with pytest.raises(ResultValidationError, match="alignment_policy"):
        publish_result(result_dir, audio=audio, request=request)

    assert not (result_dir / "manifest.json").exists()
    assert not (result_dir / "transcript.json").exists()
    assert not list(result_dir.glob(".publication-*"))


def test_existing_complete_manifest_is_never_overwritten(
    workspace_tmp_path: Path,
) -> None:
    result_dir = workspace_tmp_path / "result"
    audio, request = _publication_inputs(result_dir)
    manifest_path = publish_result(result_dir, audio=audio, request=request)
    before = {
        path.name: path.read_bytes() for path in result_dir.iterdir() if path.is_file()
    }

    with pytest.raises(ResultValidationError, match="already exists"):
        publish_result(result_dir, audio=audio, request=request)

    assert {
        path.name: path.read_bytes() for path in result_dir.iterdir() if path.is_file()
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
    manifest_path = run_transcribe(audio, **kwargs).manifest_path
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


@pytest.mark.parametrize("identity_field", ["audio_id", "config_digest"])
def test_recovery_rejects_workspace_with_wrong_identity(
    workspace_tmp_path: Path,
    identity_field: str,
) -> None:
    result_dir = workspace_tmp_path / "result"
    audio, request = _publication_inputs(result_dir)
    manifest_path = publish_result(result_dir, audio=audio, request=request)
    workspace_path = result_dir / "workspace" / "result.json"
    workspace = read_json(workspace_path)
    workspace[identity_field] = "f" * 64
    write_json_atomic(workspace_path, workspace)
    (result_dir / "transcript.json").unlink()

    with pytest.raises(ResultValidationError, match="Invalid workspace result"):
        publish_result(result_dir, audio=audio, request=request, replace_existing=True)

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

    assert not list(results.rglob("manifest.json"))


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
    ).manifest_path

    result = load_result(manifest_path)
    assert [item["text"] for item in result.transcript["items"]] == ["echo"]
    log_text = (manifest_path.parent / "transcribe.log").read_text(encoding="utf-8")
    assert log_text.count("action=drop_zero_duration_items dropped=1") == 1


def test_workspace_normalizes_public_text_and_items(workspace_tmp_path: Path) -> None:
    workspace_path = workspace_tmp_path / "result.json"

    write_workspace_result(
        workspace_path,
        audio_id="a" * 64,
        config_digest="b" * 64,
        text="Ａ臺灣。",
        items=[AlignmentItem("Ａ臺灣。", 0.0, 0.5, None)],
        duration=1.0,
        provider="faster-whisper",
        language="zh",
    )

    workspace = read_json(workspace_path)
    assert workspace["audio_id"] == "a" * 64
    assert workspace["config_digest"] == "b" * 64
    assert "schema_version" not in workspace
    assert workspace["text"] == "A台湾。"
    assert workspace["items"][0]["text"] == "A台湾。"


def test_workspace_projects_phrase_normalization_onto_items(
    workspace_tmp_path: Path,
) -> None:
    workspace_path = workspace_tmp_path / "result.json"

    write_workspace_result(
        workspace_path,
        audio_id="a" * 64,
        config_digest="b" * 64,
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
        audio_id="a" * 64,
        config_digest="b" * 64,
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
        audio_id="a" * 64,
        config_digest="b" * 64,
        text="Ａ臺灣",
        items=[AlignmentItem("Ａ臺灣", 0.0, 0.5, None)],
        duration=1.0,
        provider="faster-whisper",
        language="yue",
    )

    assert read_json(workspace_path)["text"] == "A臺灣"
