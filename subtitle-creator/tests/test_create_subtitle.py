from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = SKILL_DIR / "results"


def run_create(audio_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.create_subtitle", str(audio_path)],
        cwd=SKILL_DIR,
        capture_output=True,
        check=False,
        text=True,
    )


@pytest.fixture
def audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str]]:
    path = tmp_path / "sample.wav"
    content = b"test audio content"
    path.write_bytes(content)
    audio_id = hashlib.sha256(content).hexdigest()
    monkeypatch.setenv("SUBTITLE_CREATOR_RESULTS_DIR", str(tmp_path / "results"))
    results_dir = tmp_path / "results"
    monkeypatch.setattr(
        __import__("scripts.subtitle_job", fromlist=["RESULTS_DIR"]), "RESULTS_DIR", results_dir
    )
    monkeypatch.setitem(globals(), "RESULTS_DIR", results_dir)
    yield path, audio_id


def test_create_publishes_content_addressed_job(audio: tuple[Path, str]) -> None:
    audio_path, audio_id = audio

    result = run_create(audio_path)

    job_path = (RESULTS_DIR / audio_id / "subtitle_job.json").resolve()
    assert result.returncode == 0
    assert result.stdout == f"subtitle_job: {job_path}\n"
    assert result.stderr == ""
    assert json.loads(job_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "job_id": audio_id,
        "status": "needs_transcription",
        "audio": {"path": str(audio_path.resolve()), "id": audio_id},
        "transcription": None,
        "artifacts": None,
        "changed_segment_ids": [],
    }


def test_create_reuses_job_and_rebinds_same_content_to_new_path(
    audio: tuple[Path, str], tmp_path: Path
) -> None:
    audio_path, audio_id = audio
    assert run_create(audio_path).returncode == 0
    job_path = RESULTS_DIR / audio_id / "subtitle_job.json"
    original_mtime = job_path.stat().st_mtime_ns

    unchanged = run_create(audio_path)
    assert unchanged.returncode == 0
    assert job_path.stat().st_mtime_ns == original_mtime

    moved_path = tmp_path / "renamed.wav"
    moved_path.write_bytes(audio_path.read_bytes())
    rebound = run_create(moved_path)

    assert rebound.returncode == 0
    assert json.loads(job_path.read_text(encoding="utf-8"))["audio"]["path"] == str(
        moved_path.resolve()
    )


def test_create_rejects_invalid_existing_job_without_overwriting(
    audio: tuple[Path, str],
) -> None:
    audio_path, audio_id = audio
    job_path = RESULTS_DIR / audio_id / "subtitle_job.json"
    job_path.parent.mkdir(parents=True)
    invalid_content = '{"schema_version": 99}\n'
    job_path.write_text(invalid_content, encoding="utf-8")

    result = run_create(audio_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr
    assert job_path.read_text(encoding="utf-8") == invalid_content


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_create_rejects_non_file_audio(tmp_path: Path, kind: str) -> None:
    audio_path = tmp_path / "not-a-file"
    if kind == "directory":
        audio_path.mkdir()

    result = run_create(audio_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr


def test_create_keeps_job_unpublished_when_atomic_replace_fails(
    audio: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import subtitle_job

    audio_path, audio_id = audio

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("fail")

    monkeypatch.setattr(subtitle_job.os, "replace", fail_replace)

    from scripts.create_subtitle import main

    assert main([str(audio_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
    job_dir = RESULTS_DIR / audio_id
    assert not (job_dir / "subtitle_job.json").exists()
    assert list(job_dir.glob("*.tmp")) == []
