from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import attach_transcription, subtitle_job
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
    monkeypatch.setattr(subtitle_job, "RESULTS_DIR", results_dir)
    job_dir = results_dir / audio_id
    job_dir.mkdir(parents=True)
    job_path = job_dir / "subtitle_job.json"
    job_path.write_bytes(
        json_bytes(
            {
                "schema_version": 2,
                "job_id": audio_id,
                "status": "needs_transcription",
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


def test_attach_imports_local_editable_snapshot(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, audio_id = subtitle_task
    monkeypatch.setattr(
        attach_transcription,
        "load_transcription",
        lambda _path: sample_transcription(audio_id),
    )

    normalized_path = attach_transcription.attach_transcription(job_path, manifest_path)

    baseline_path = job_path.parent / "normalized_transcript.before_correction.json"
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
    assert job["status"] == "editable"
    assert "transcription" not in job
    assert job["artifacts"]["before_correction_sha256"] == subtitle_job.sha256_file(baseline_path)


def test_attach_input_failure_does_not_publish(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, _audio_id = subtitle_task
    before = job_path.read_bytes()

    def fail(_path: Path) -> TranscriptionInput:
        raise TranscriptionInputError("invalid transcription")

    monkeypatch.setattr(attach_transcription, "load_transcription", fail)

    with pytest.raises(TranscriptionInputError):
        attach_transcription.attach_transcription(job_path, manifest_path)

    assert job_path.read_bytes() == before
    assert not (job_path.parent / "normalized_transcript.json").exists()


def test_attach_rejects_transcription_for_different_audio(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, _audio_id = subtitle_task
    monkeypatch.setattr(
        attach_transcription,
        "load_transcription",
        lambda _path: sample_transcription("a" * 64),
    )

    with pytest.raises(SubtitleJobError, match="audio identity does not match"):
        attach_transcription.attach_transcription(job_path, manifest_path)

    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "needs_transcription"


def test_attach_requires_absolute_input_paths(subtitle_task: tuple[Path, Path, Path, str]) -> None:
    job_path, manifest_path, _audio_path, _audio_id = subtitle_task
    with pytest.raises(SubtitleJobError, match="absolute"):
        attach_transcription.attach_transcription(Path("subtitle_job.json"), manifest_path)
    with pytest.raises(SubtitleJobError, match="absolute"):
        attach_transcription.attach_transcription(job_path, Path("manifest.json"))


@pytest.mark.parametrize("kind", ["missing", "changed"])
def test_attach_rechecks_original_audio(
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
        attach_transcription,
        "load_transcription",
        lambda _path: sample_transcription(audio_id),
    )

    with pytest.raises(SubtitleJobError):
        attach_transcription.attach_transcription(job_path, manifest_path)


def test_attach_editable_reuses_local_snapshot_without_reading_upstream(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, audio_id = subtitle_task
    monkeypatch.setattr(
        attach_transcription,
        "load_transcription",
        lambda _path: sample_transcription(audio_id),
    )
    normalized_path = attach_transcription.attach_transcription(job_path, manifest_path)
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    normalized["segments"][0]["text"] = "Agent corrected text"
    normalized_path.write_bytes(json_bytes(normalized))
    corrected_bytes = normalized_path.read_bytes()

    def unexpected(_path: Path) -> TranscriptionInput:
        raise AssertionError("editable jobs must not reload upstream transcription")

    monkeypatch.setattr(attach_transcription, "load_transcription", unexpected)
    other_manifest = (manifest_path.parent / "replacement.json").resolve()

    assert attach_transcription.attach_transcription(job_path, other_manifest) == normalized_path
    assert normalized_path.read_bytes() == corrected_bytes


def test_attach_publishes_job_last_and_retry_overwrites_unpublished_residue(
    subtitle_task: tuple[Path, Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, manifest_path, _audio_path, audio_id = subtitle_task
    monkeypatch.setattr(
        attach_transcription,
        "load_transcription",
        lambda _path: sample_transcription(audio_id),
    )
    original_write = attach_transcription.atomic_write_json

    def fail_job_write(path: Path, value: dict[str, Any]) -> None:
        if path == job_path:
            raise OSError("fail publish")
        original_write(path, value)

    monkeypatch.setattr(attach_transcription, "atomic_write_json", fail_job_write)
    with pytest.raises(OSError, match="fail publish"):
        attach_transcription.attach_transcription(job_path, manifest_path)

    normalized_path = job_path.parent / "normalized_transcript.json"
    baseline_path = job_path.parent / "normalized_transcript.before_correction.json"
    normalized_path.write_text("unpublished residue", encoding="utf-8")
    baseline_path.write_text("unpublished residue", encoding="utf-8")
    monkeypatch.setattr(attach_transcription, "atomic_write_json", original_write)

    assert attach_transcription.attach_transcription(job_path, manifest_path) == normalized_path
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "editable"
