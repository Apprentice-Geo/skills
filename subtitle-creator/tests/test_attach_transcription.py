from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = SKILL_DIR / "results"
VARIANT_ID = "b" * 64


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def run_attach(job_path: Path | str, manifest_path: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.attach_transcription",
            str(job_path),
            "--transcription-manifest",
            str(manifest_path),
        ],
        cwd=SKILL_DIR,
        capture_output=True,
        check=False,
        text=True,
    )


@pytest.fixture
def transcription(
    tmp_path: Path,
) -> Iterator[tuple[Path, Path, Path, str, dict[str, Any]]]:
    audio_path = tmp_path / "sample.wav"
    audio_content = b"test audio content"
    audio_path.write_bytes(audio_content)
    audio_id = hashlib.sha256(audio_content).hexdigest()
    job_dir = RESULTS_DIR / audio_id
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True)
    job_path = job_dir / "subtitle_job.json"
    job = {
        "schema_version": 1,
        "job_id": audio_id,
        "status": "needs_transcription",
        "audio": {"path": str(audio_path.resolve()), "id": audio_id},
        "transcription": None,
        "artifacts": None,
        "changed_segment_ids": [],
    }
    job_path.write_bytes(json_bytes(job))

    result_dir = tmp_path / "upstream"
    result_dir.mkdir()
    transcript_path = result_dir / "transcript.json"
    transcript = {
        "schema_version": 1,
        "audio_id": audio_id,
        "variant_id": VARIANT_ID,
        "provider": "faster-whisper",
        "language": "zh",
        "duration": 12.3,
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.2, "text": "  第一段\n  文本  "},
            {"id": 1, "start": 1.2, "end": 2.5, "text": "第二\t\t段"},
        ],
    }
    transcript_path.write_bytes(json_bytes(transcript))
    manifest_path = result_dir / "result_manifest.json"
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "audio": {"id": audio_id, "duration": 12.3},
        "request": {
            "variant_id": VARIANT_ID,
            "provider": "faster-whisper",
            "language": "zh",
        },
        "artifacts": {
            "transcript": "transcript.json",
            "raw_timestamps": "../must-not-be-read.json",
            "log": "missing.log",
            "workspace": "missing-workspace",
        },
        "artifact_sha256": {"transcript": hashlib.sha256(transcript_path.read_bytes()).hexdigest()},
    }
    manifest_path.write_bytes(json_bytes(manifest))
    yield job_path, manifest_path, transcript_path, audio_id, manifest
    shutil.rmtree(job_dir, ignore_errors=True)


def test_attach_normalizes_and_publishes_editable(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
) -> None:
    job_path, manifest_path, transcript_path, audio_id, _manifest = transcription
    upstream_bytes = transcript_path.read_bytes()

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    normalized_path = job_path.parent / "normalized_transcript.json"
    baseline_path = job_path.parent / "normalized_transcript.before_correction.json"
    expected = {
        "schema_version": 1,
        "source": {
            "manifest_path": str(manifest_path.resolve()),
            "audio_id": audio_id,
            "variant_id": VARIANT_ID,
        },
        "provider": "faster-whisper",
        "language": "zh",
        "duration": 12.3,
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.2, "text": "第一段 文本"},
            {"id": 1, "start": 1.2, "end": 2.5, "text": "第二 段"},
        ],
    }
    assert result.returncode == 0
    assert result.stdout == f"normalized_transcript: {normalized_path.resolve()}\n"
    assert result.stderr == ""
    assert normalized_path.read_bytes() == baseline_path.read_bytes()
    assert not normalized_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(normalized_path.read_text(encoding="utf-8")) == expected
    assert transcript_path.read_bytes() == upstream_bytes

    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["status"] == "editable"
    assert job["transcription"] == {
        "manifest_path": str(manifest_path.resolve()),
        "variant_id": VARIANT_ID,
    }
    assert job["artifacts"] == {
        "normalized_transcript": str(normalized_path.resolve()),
        "normalized_transcript_sha256": None,
        "before_correction": str(baseline_path.resolve()),
        "before_correction_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "subtitle": None,
        "subtitle_sha256": None,
    }
    assert job["changed_segment_ids"] == []


@pytest.mark.parametrize("artifact_path", ["../transcript.json", "ABSOLUTE"])
def test_attach_rejects_unsafe_transcript_path(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
    artifact_path: str,
) -> None:
    job_path, manifest_path, _transcript_path, _audio_id, manifest = transcription
    manifest["artifacts"]["transcript"] = (
        str((manifest_path.parent / "transcript.json").resolve())
        if artifact_path == "ABSOLUTE"
        else artifact_path
    )
    manifest_path.write_bytes(json_bytes(manifest))

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "needs_transcription"


def test_attach_rejects_missing_transcript(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
) -> None:
    job_path, manifest_path, transcript_path, _audio_id, _manifest = transcription
    transcript_path.unlink()

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("manifest_schema", 2),
        ("manifest_status", "failed"),
        ("manifest_audio_id", "a" * 64),
        ("transcript_digest", "0" * 64),
        ("transcript_audio_id", "a" * 64),
        ("transcript_variant_id", "c" * 64),
        ("transcript_provider", "qwen3"),
        ("transcript_language", "en"),
        ("transcript_duration", 10.0),
        ("transcript_segments", "invalid"),
    ],
)
def test_attach_rejects_invalid_public_contract(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
    target: str,
    value: object,
) -> None:
    job_path, manifest_path, transcript_path, _audio_id, manifest = transcription
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    if target == "manifest_schema":
        manifest["schema_version"] = value
    elif target == "manifest_status":
        manifest["status"] = value
    elif target == "manifest_audio_id":
        manifest["audio"]["id"] = value
    elif target == "transcript_digest":
        manifest["artifact_sha256"]["transcript"] = value
    else:
        transcript[target.removeprefix("transcript_")] = value
        transcript_path.write_bytes(json_bytes(transcript))
        manifest["artifact_sha256"]["transcript"] = hashlib.sha256(
            transcript_path.read_bytes()
        ).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "needs_transcription"


@pytest.mark.parametrize("which", ["manifest", "transcript"])
def test_attach_rejects_duplicate_keys_and_nan(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
    which: str,
) -> None:
    job_path, manifest_path, transcript_path, _audio_id, manifest = transcription
    if which == "manifest":
        manifest_path.write_text(
            '{"schema_version":1,"schema_version":1,"status":"complete"}',
            encoding="utf-8",
        )
    else:
        transcript_path.write_text('{"schema_version":1,"duration":NaN}', encoding="utf-8")
        manifest["artifact_sha256"]["transcript"] = hashlib.sha256(
            transcript_path.read_bytes()
        ).hexdigest()
        manifest_path.write_bytes(json_bytes(manifest))

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""


def test_attach_requires_absolute_input_paths(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
) -> None:
    job_path, manifest_path, _transcript_path, _audio_id, _manifest = transcription

    relative_job = job_path.relative_to(SKILL_DIR)

    relative_job_result = run_attach(relative_job, manifest_path.resolve())
    relative_manifest_result = run_attach(job_path.resolve(), Path("result_manifest.json"))

    assert relative_job_result.returncode == 1
    assert "absolute" in relative_job_result.stderr
    assert relative_manifest_result.returncode == 1
    assert "absolute" in relative_manifest_result.stderr


@pytest.mark.parametrize("kind", ["missing", "changed"])
def test_attach_rechecks_original_audio(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]], kind: str
) -> None:
    job_path, manifest_path, _transcript_path, _audio_id, _manifest = transcription
    job = json.loads(job_path.read_text(encoding="utf-8"))
    audio_path = Path(job["audio"]["path"])
    if kind == "missing":
        audio_path.unlink()
    else:
        audio_path.write_bytes(b"changed")

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "needs_transcription"


@pytest.mark.parametrize("invalid", ["job_path", "job_id", "audio_id", "artifact_escape"])
def test_attach_rejects_invalid_job_identity_and_artifact_paths(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
    tmp_path: Path,
    invalid: str,
) -> None:
    job_path, manifest_path, _transcript_path, _audio_id, _manifest = transcription
    if invalid == "artifact_escape":
        assert run_attach(job_path.resolve(), manifest_path.resolve()).returncode == 0
    job = json.loads(job_path.read_text(encoding="utf-8"))
    input_path = job_path
    if invalid == "job_path":
        input_path = tmp_path / "wrong" / "subtitle_job.json"
        input_path.parent.mkdir()
        input_path.write_bytes(json_bytes(job))
    elif invalid == "job_id":
        job["job_id"] = "c" * 64
        job_path.write_bytes(json_bytes(job))
    elif invalid == "audio_id":
        job["audio"]["id"] = "c" * 64
        job_path.write_bytes(json_bytes(job))
    else:
        job["artifacts"]["normalized_transcript"] = str(
            (manifest_path.parent / "outside.json").resolve()
        )
        job_path.write_bytes(json_bytes(job))

    result = run_attach(input_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""


def test_attach_same_variant_reuses_without_overwriting_corrections(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
) -> None:
    job_path, manifest_path, transcript_path, _audio_id, manifest = transcription
    assert run_attach(job_path.resolve(), manifest_path.resolve()).returncode == 0
    normalized_path = job_path.parent / "normalized_transcript.json"
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    normalized["segments"][0]["text"] = "Agent corrected text"
    normalized_path.write_bytes(json_bytes(normalized))
    corrected_bytes = normalized_path.read_bytes()

    reused = run_attach(job_path.resolve(), manifest_path.resolve())
    assert reused.returncode == 0
    assert reused.stdout == f"normalized_transcript: {normalized_path.resolve()}\n"
    assert normalized_path.read_bytes() == corrected_bytes

    manifest["request"]["variant_id"] = "c" * 64
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript["variant_id"] = "c" * 64
    transcript_path.write_bytes(json_bytes(transcript))
    manifest["artifact_sha256"]["transcript"] = hashlib.sha256(
        transcript_path.read_bytes()
    ).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))

    rejected = run_attach(job_path.resolve(), manifest_path.resolve())
    assert rejected.returncode == 1
    assert normalized_path.read_bytes() == corrected_bytes


def test_attach_publishes_job_last_and_retry_overwrites_unpublished_residue(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import attach_transcription

    job_path, manifest_path, _transcript_path, _audio_id, _manifest = transcription
    original_write = attach_transcription.atomic_write_json

    def fail_job_write(path: Path, value: dict[str, Any]) -> None:
        if path == job_path:
            raise OSError("fail publish")
        original_write(path, value)

    monkeypatch.setattr(attach_transcription, "atomic_write_json", fail_job_write)

    with pytest.raises(OSError, match="fail publish"):
        attach_transcription.attach_transcription(job_path.resolve(), manifest_path.resolve())

    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "needs_transcription"
    normalized_path = job_path.parent / "normalized_transcript.json"
    baseline_path = job_path.parent / "normalized_transcript.before_correction.json"
    assert normalized_path.exists()
    assert baseline_path.exists()
    normalized_path.write_text("unpublished residue", encoding="utf-8")
    baseline_path.write_text("unpublished residue", encoding="utf-8")

    monkeypatch.setattr(attach_transcription, "atomic_write_json", original_write)
    assert (
        attach_transcription.attach_transcription(job_path.resolve(), manifest_path.resolve())
        == normalized_path.resolve()
    )
    assert json.loads(normalized_path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "editable"
