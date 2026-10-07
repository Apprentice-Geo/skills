from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts import bind_transcription, subtitle_job
from scripts.subtitle_job import SubtitleJobError
from scripts.transcription_input import TranscriptionInput, TranscriptionInputError


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


@pytest.fixture
def subtitle_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, str]:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"test audio content")
    audio_id = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    results_dir = tmp_path / "results"
    monkeypatch.setenv("SUBTITLE_CREATOR_DATA_DIR", str(results_dir.parent))
    job_dir = results_dir / audio_id
    job_dir.mkdir(parents=True)
    job_path = job_dir / "subtitle_job.json"
    job_path.write_bytes(
        json_bytes(
            {
                "schema_version": 3,
                "job_id": audio_id,
                "status": "transcription_unbound",
                "audio": {"path": str(audio_path.resolve()), "id": audio_id},
                "artifacts": None,
                "changed_segment_ids": [],
            }
        )
    )
    manifest_path = (tmp_path / "upstream" / "manifest.json").resolve()
    manifest_path.parent.mkdir()
    manifest_path.write_text("opaque", encoding="utf-8")
    return job_path, manifest_path, audio_path, audio_id


def sample_transcription(audio_id: str) -> TranscriptionInput:
    return TranscriptionInput(
        audio_id=audio_id,
        provider="faster-whisper",
        language="zh",
        duration=12.3,
        segments=[
            {"id": 0, "start": 0.0, "end": 1.2, "text": "  第一段\n  文本  "},
            {"id": 1, "start": 1.2, "end": 2.5, "text": "第二\t\t段"},
            {"id": 2, "start": 2.5, "end": 3.0, "text": "   "},
        ],
    )


def test_bind_imports_local_transcription_bound_snapshot(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, audio_id = subtitle_task
    monkeypatch.setattr(
        bind_transcription,
        "load_transcription",
        lambda _path: sample_transcription(audio_id),
    )

    normalized_path = bind_transcription.bind_transcription(job_path, manifest_path)

    baseline_path = Path(subtitle_job.read_json_object(job_path)["artifacts"]["before_correction"])
    expected = {
        "schema_version": 2,
        "source": "audio_transcribe",
        "provider": "faster-whisper",
        "language": "zh",
        "duration": 12.3,
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.2, "text": "第一段 文本"},
            {"id": 1, "start": 1.2, "end": 2.5, "text": "第二 段"},
        ],
    }
    assert json.loads(normalized_path.read_text(encoding="utf-8")) == expected
    assert normalized_path.read_bytes() == baseline_path.read_bytes()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["status"] == "transcription_bound"
    assert "transcription" not in job
    assert job["artifacts"]["before_correction_sha256"] == subtitle_job.sha256_file(baseline_path)


def test_bind_input_failure_does_not_publish(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, _audio_id = subtitle_task
    before = job_path.read_bytes()

    def fail(_path: Path) -> TranscriptionInput:
        raise TranscriptionInputError("invalid transcription")

    monkeypatch.setattr(bind_transcription, "load_transcription", fail)

    with pytest.raises(TranscriptionInputError):
        bind_transcription.bind_transcription(job_path, manifest_path)

    assert job_path.read_bytes() == before
    assert not (job_path.parent / "normalized_transcript.json").exists()


def test_bind_rejects_transcription_for_different_audio(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, _audio_id = subtitle_task
    monkeypatch.setattr(
        bind_transcription,
        "load_transcription",
        lambda _path: sample_transcription("a" * 64),
    )

    with pytest.raises(SubtitleJobError, match="audio identity does not match"):
        bind_transcription.bind_transcription(job_path, manifest_path)

    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "transcription_unbound"


def test_bind_requires_absolute_input_paths(subtitle_task: tuple[Path, Path, Path, str]) -> None:
    job_path, manifest_path, _audio_path, _audio_id = subtitle_task
    with pytest.raises(SubtitleJobError, match="absolute"):
        bind_transcription.bind_transcription(Path("subtitle_job.json"), manifest_path)
    with pytest.raises(SubtitleJobError, match="absolute"):
        bind_transcription.bind_transcription(job_path, Path("manifest.json"))


@pytest.mark.parametrize("kind", ["missing", "changed"])
def test_bind_rechecks_original_audio(
    subtitle_task: tuple[Path, Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    job_path, manifest_path, audio_path, audio_id = subtitle_task
    if kind == "missing":
        audio_path.unlink()
    else:
        audio_path.write_bytes(b"changed")
    monkeypatch.setattr(
        bind_transcription,
        "load_transcription",
        lambda _path: sample_transcription(audio_id),
    )

    with pytest.raises(SubtitleJobError):
        bind_transcription.bind_transcription(job_path, manifest_path)


@pytest.mark.parametrize("same_manifest", [True, False])
def test_bind_always_reimports_and_invalidates_subtitle(subtitle_task, monkeypatch, same_manifest):
    from scripts import generate_srt

    job_path, manifest_path, _audio, audio_id = subtitle_task
    monkeypatch.setattr(
        bind_transcription, "load_transcription", lambda _: sample_transcription(audio_id)
    )
    first_path = bind_transcription.bind_transcription(job_path, manifest_path)
    normalized = subtitle_job.read_json_object(first_path)
    normalized["segments"][0]["text"] = "corrected"
    first_path.write_bytes(json_bytes(normalized))
    generate_srt.generate_srt(job_path)
    replacement = replace(
        sample_transcription(audio_id),
        segments=[{"id": 0, "start": 1, "end": 3, "text": "new transcription"}],
    )
    monkeypatch.setattr(bind_transcription, "load_transcription", lambda _: replacement)
    target = manifest_path if same_manifest else manifest_path.parent / "replacement.json"
    target.write_text("opaque")

    new_path = bind_transcription.bind_transcription(job_path, target)

    job = subtitle_job.read_json_object(job_path)
    assert job["artifacts"]["subtitle"] is None
    assert job["changed_segment_ids"] == []
    assert subtitle_job.read_json_object(new_path)["segments"] == replacement.segments
    assert new_path.read_bytes() == Path(job["artifacts"]["before_correction"]).read_bytes()
    assert first_path.read_bytes() == json_bytes(normalized)


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("failure", ["write", "publish"])
def test_bind_failure_preserves_previous_job_and_retry_succeeds(
    subtitle_task, monkeypatch, bound, failure
):
    job_path, manifest_path, _audio, audio_id = subtitle_task
    monkeypatch.setattr(
        bind_transcription, "load_transcription", lambda _: sample_transcription(audio_id)
    )
    if bound:
        normalized_path = bind_transcription.bind_transcription(job_path, manifest_path)
        normalized = subtitle_job.read_json_object(normalized_path)
        normalized["segments"][0]["text"] = "keep correction"
        normalized_path.write_bytes(json_bytes(normalized))
    before = job_path.read_bytes()
    old_artifacts = subtitle_job.read_json_object(job_path)["artifacts"]
    old_bytes = (
        {
            field: Path(old_artifacts[field]).read_bytes()
            for field in ("normalized_transcript", "before_correction")
        }
        if old_artifacts
        else {}
    )
    with monkeypatch.context() as patch:
        if failure == "publish":

            def fail_publish(*args, **kwargs):
                raise OSError("fail publish")

            patch.setattr(subtitle_job, "atomic_write_json", fail_publish)
        else:
            original_open = Path.open

            def fail_write(path, *args, **kwargs):
                if path.name == subtitle_job.NORMALIZED_FILENAME and args == ("wb",):
                    raise OSError("fail write")
                return original_open(path, *args, **kwargs)

            patch.setattr(Path, "open", fail_write)
        with pytest.raises(OSError, match="fail"):
            bind_transcription.bind_transcription(job_path, manifest_path)
    assert job_path.read_bytes() == before
    for field, content in old_bytes.items():
        assert Path(old_artifacts[field]).read_bytes() == content
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path), allow_stale_derived=True)
    new_path = bind_transcription.bind_transcription(job_path, manifest_path)
    assert new_path.is_file()
    assert subtitle_job.read_json_object(job_path)["status"] == "transcription_bound"


@pytest.mark.parametrize("damage", ["baseline", "missing", "timeline"])
def test_bind_replaces_damaged_old_artifacts(subtitle_task, monkeypatch, damage):
    job_path, manifest_path, _audio, audio_id = subtitle_task
    monkeypatch.setattr(
        bind_transcription, "load_transcription", lambda _: sample_transcription(audio_id)
    )
    path = bind_transcription.bind_transcription(job_path, manifest_path)
    if damage == "baseline":
        job = subtitle_job.read_json_object(job_path)
        Path(job["artifacts"]["before_correction"]).write_bytes(b"damaged")
    elif damage == "missing":
        path.unlink()
    else:
        normalized = subtitle_job.read_json_object(path)
        normalized["segments"][0]["end"] = 999
        path.write_bytes(json_bytes(normalized))
    bind_transcription.bind_transcription(job_path, manifest_path)
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))


@pytest.mark.parametrize("failure", ["audio_identity", "invalid_segments", "missing_manifest"])
def test_bind_rejects_invalid_replacement_without_changing_bound_job(
    subtitle_task, monkeypatch, failure
):
    job_path, manifest_path, _audio, audio_id = subtitle_task
    monkeypatch.setattr(
        bind_transcription, "load_transcription", lambda _: sample_transcription(audio_id)
    )
    normalized_path = bind_transcription.bind_transcription(job_path, manifest_path)
    before = job_path.read_bytes()
    old_text = normalized_path.read_bytes()
    if failure == "audio_identity":
        replacement = sample_transcription("a" * 64)
    else:
        replacement = replace(
            sample_transcription(audio_id),
            segments=[{"id": 0, "start": 1, "end": 1, "text": "invalid"}],
        )
    monkeypatch.setattr(bind_transcription, "load_transcription", lambda _: replacement)
    if failure == "missing_manifest":
        manifest_path.unlink()
    with pytest.raises(SubtitleJobError):
        bind_transcription.bind_transcription(job_path, manifest_path)
    assert job_path.read_bytes() == before
    assert normalized_path.read_bytes() == old_text
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))


def test_bind_cli_keeps_exact_result_and_moves_log(
    subtitle_task: tuple[Path, Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    job_path, manifest_path, _audio_path, audio_id = subtitle_task
    monkeypatch.setattr(
        bind_transcription,
        "load_transcription",
        lambda _path: sample_transcription(audio_id),
    )

    assert (
        bind_transcription.main(
            [str(job_path.resolve()), "--transcription-manifest", str(manifest_path.resolve())]
        )
        == 0
    )

    terminal = capsys.readouterr()
    normalized_path = Path(
        subtitle_job.read_json_object(job_path)["artifacts"]["normalized_transcript"]
    )
    assert terminal.out == f"normalized_transcript: {normalized_path}\n"
    assert terminal.err == ""
    logs = list(job_path.parent.glob("bind-transcription-*.log"))
    assert len(logs) == 1
    assert terminal.out.strip() in logs[0].read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox directory permissions")
def test_bind_and_reset_work_when_private_directory_creation_is_unusable(
    subtitle_task, monkeypatch
):
    from scripts import transcript

    job_path, manifest_path, _audio, audio_id = subtitle_task
    original_mkdir = os.mkdir

    def deny_private_directory(path, mode=0o777, *, dir_fd=None):
        if mode == 0o700:
            raise PermissionError("private directory ACL excludes sandbox identity")
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", deny_private_directory)
    monkeypatch.setattr(
        bind_transcription, "load_transcription", lambda _: sample_transcription(audio_id)
    )
    path = bind_transcription.bind_transcription(job_path, manifest_path)
    path.write_bytes(json_bytes({**subtitle_job.read_json_object(path), "provider": "damaged"}))
    restored = transcript.operate("reset", job_path, "all")
    assert restored.is_file()
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))
