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


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def update_artifact_digests(manifest_path: Path, manifest: dict[str, Any]) -> None:
    for name in ("transcript", "raw_timestamps"):
        artifact_path = manifest_path.parent / manifest["artifacts"][name]
        manifest["artifact_sha256"][name] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))


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
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, Path, Path, str, dict[str, Any]]]:
    audio_path = tmp_path / "sample.wav"
    audio_content = b"test audio content"
    audio_path.write_bytes(audio_content)
    audio_id = hashlib.sha256(audio_content).hexdigest()
    monkeypatch.setenv("SUBTITLE_CREATOR_RESULTS_DIR", str(tmp_path / "results"))
    results_dir = tmp_path / "results"
    monkeypatch.setattr(
        __import__("scripts.subtitle_job", fromlist=["RESULTS_DIR"]), "RESULTS_DIR", results_dir
    )
    monkeypatch.setitem(globals(), "RESULTS_DIR", results_dir)
    job_dir = RESULTS_DIR / audio_id
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
    (result_dir / "workspace").mkdir()
    (result_dir / "transcription.log").write_text("complete\n", encoding="utf-8")
    request = {"provider": "faster-whisper", "language": "zh"}
    variant_id = canonical_sha256(request)
    transcript_path = result_dir / "transcript.json"
    transcript = {
        "schema_version": 1,
        "audio_id": audio_id,
        "variant_id": variant_id,
        "provider": "faster-whisper",
        "language": "zh",
        "duration": 12.3,
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.2, "text": "  第一段\n  文本  "},
            {"id": 1, "start": 1.2, "end": 2.5, "text": "第二\t\t段"},
            {"id": 2, "start": 2.5, "end": 3.0, "text": "   "},
        ],
    }
    transcript_path.write_bytes(json_bytes(transcript))
    raw_path = result_dir / "raw_timestamps.json"
    raw_path.write_bytes(
        json_bytes(
            {
                "schema_version": 1,
                "audio_id": audio_id,
                "variant_id": variant_id,
                "provider": "faster-whisper",
                "language": "zh",
                "duration": 12.3,
                "items": [
                    {
                        "text": "第一段文本第二段",
                        "start": 0.0,
                        "end": 3.0,
                        "probability": 0.9,
                    }
                ],
            }
        )
    )
    manifest_path = result_dir / "result_manifest.json"
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "audio": {
            "id": audio_id,
            "size": len(audio_content),
            "sample_count": 123,
            "sample_rate": 10,
            "duration": 12.3,
        },
        "request": {"variant_id": variant_id, **request},
        "artifacts": {
            "transcript": "transcript.json",
            "raw_timestamps": "raw_timestamps.json",
            "log": "transcription.log",
            "workspace": "workspace",
        },
        "artifact_sha256": {
            "transcript": hashlib.sha256(transcript_path.read_bytes()).hexdigest(),
            "raw_timestamps": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        },
    }
    manifest_path.write_bytes(json_bytes(manifest))
    yield job_path, manifest_path, transcript_path, audio_id, manifest
    shutil.rmtree(job_dir, ignore_errors=True)


def test_attach_normalizes_and_publishes_editable(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
) -> None:
    job_path, manifest_path, transcript_path, audio_id, manifest = transcription
    upstream_bytes = transcript_path.read_bytes()

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    normalized_path = job_path.parent / "normalized_transcript.json"
    baseline_path = job_path.parent / "normalized_transcript.before_correction.json"
    expected = {
        "schema_version": 1,
        "source": {
            "manifest_path": str(manifest_path.resolve()),
            "audio_id": audio_id,
            "variant_id": manifest["request"]["variant_id"],
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
        "variant_id": manifest["request"]["variant_id"],
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


def test_attach_propagates_contract_error_without_publishing(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
) -> None:
    job_path, manifest_path, _transcript_path, _audio_id, manifest = transcription
    raw_path = manifest_path.parent / manifest["artifacts"]["raw_timestamps"]
    raw_path.write_text('{"schema_version": 1}\n', encoding="utf-8")

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert "raw_timestamps artifact digest mismatch" in result.stderr
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "needs_transcription"


def test_attach_rejects_transcription_for_different_audio(
    transcription: tuple[Path, Path, Path, str, dict[str, Any]],
) -> None:
    job_path, manifest_path, transcript_path, _audio_id, manifest = transcription
    other_audio_id = "a" * 64
    manifest["audio"]["id"] = other_audio_id
    for artifact_name in ("transcript", "raw_timestamps"):
        artifact_path = manifest_path.parent / manifest["artifacts"][artifact_name]
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["audio_id"] = other_audio_id
        artifact_path.write_bytes(json_bytes(artifact))
    update_artifact_digests(manifest_path, manifest)

    result = run_attach(job_path.resolve(), manifest_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert "audio identity does not match" in result.stderr
    assert transcript_path.is_file()
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "needs_transcription"


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
    job_path, manifest_path, _transcript_path, _audio_id, manifest = transcription
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

    request = {"provider": "faster-whisper", "language": "en"}
    variant_id = canonical_sha256(request)
    manifest["request"] = {"variant_id": variant_id, **request}
    for artifact_name in ("transcript", "raw_timestamps"):
        artifact_path = manifest_path.parent / manifest["artifacts"][artifact_name]
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["variant_id"] = variant_id
        artifact["language"] = "en"
        artifact_path.write_bytes(json_bytes(artifact))
    update_artifact_digests(manifest_path, manifest)

    rejected = run_attach(job_path.resolve(), manifest_path.resolve())
    assert rejected.returncode == 1
    assert "different transcription variant" in rejected.stderr
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
