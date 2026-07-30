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


def run_finalize(job_path: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.finalize_subtitle", str(job_path)],
        cwd=SKILL_DIR,
        capture_output=True,
        check=False,
        text=True,
    )


def run_attach(job_path: Path, manifest_path: Path) -> subprocess.CompletedProcess[str]:
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
def correction_job(
    tmp_path: Path,
) -> Iterator[tuple[Path, Path, Path, Path, dict[str, Any]]]:
    audio_path = tmp_path / "sample.wav"
    audio_content = b"finalize audio"
    audio_path.write_bytes(audio_content)
    audio_id = hashlib.sha256(audio_content).hexdigest()
    job_dir = RESULTS_DIR / audio_id
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True)

    result_dir = tmp_path / "upstream"
    result_dir.mkdir()
    transcript_path = result_dir / "transcript.json"
    transcript = {
        "schema_version": 1,
        "audio_id": audio_id,
        "variant_id": VARIANT_ID,
        "provider": "faster-whisper",
        "language": "zh",
        "duration": 360062,
        "segments": [
            {"id": 0, "start": 0.0004, "end": 1.9995, "text": "第一段"},
            {
                "id": 1,
                "start": 1.9995,
                "end": 360061.9995,
                "text": "Second line --> x",
            },
        ],
    }
    transcript_path.write_bytes(json_bytes(transcript))
    manifest_path = result_dir / "result_manifest.json"
    manifest = {
        "schema_version": 1,
        "status": "complete",
        "audio": {"id": audio_id, "duration": 360062},
        "request": {
            "variant_id": VARIANT_ID,
            "provider": "faster-whisper",
            "language": "zh",
        },
        "artifacts": {"transcript": "transcript.json"},
        "artifact_sha256": {"transcript": hashlib.sha256(transcript_path.read_bytes()).hexdigest()},
    }
    manifest_path.write_bytes(json_bytes(manifest))

    baseline = {
        "schema_version": 1,
        "source": {
            "manifest_path": str(manifest_path.resolve()),
            "audio_id": audio_id,
            "variant_id": VARIANT_ID,
        },
        "provider": "faster-whisper",
        "language": "zh",
        "duration": 360062,
        "segments": [
            {"id": 0, "start": 0.0004, "end": 1.9995, "text": "第一段"},
            {
                "id": 1,
                "start": 1.9995,
                "end": 360061.9995,
                "text": "Second line --> x",
            },
        ],
    }
    baseline_path = job_dir / "normalized_transcript.before_correction.json"
    normalized_path = job_dir / "normalized_transcript.json"
    baseline_path.write_bytes(json_bytes(baseline))
    normalized_path.write_bytes(json_bytes(baseline))
    job_path = job_dir / "subtitle_job.json"
    job = {
        "schema_version": 1,
        "job_id": audio_id,
        "status": "editable",
        "audio": {"path": str(audio_path.resolve()), "id": audio_id},
        "transcription": {
            "manifest_path": str(manifest_path.resolve()),
            "variant_id": VARIANT_ID,
        },
        "artifacts": {
            "normalized_transcript": str(normalized_path.resolve()),
            "normalized_transcript_sha256": None,
            "before_correction": str(baseline_path.resolve()),
            "before_correction_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "subtitle": None,
            "subtitle_sha256": None,
        },
        "changed_segment_ids": [],
    }
    job_path.write_bytes(json_bytes(job))
    yield job_path, normalized_path, baseline_path, manifest_path, manifest
    shutil.rmtree(job_dir, ignore_errors=True)


def test_finalize_publishes_corrected_srt_and_keeps_editable_job(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, normalized_path, baseline_path, _manifest_path, _manifest = correction_job
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    normalized["segments"][0]["text"] = "  修正\n字幕  "
    normalized["segments"][1]["text"] = " Second\t line --> x "
    normalized_path.write_bytes(json_bytes(normalized))

    result = run_finalize(job_path.resolve())

    subtitle_path = job_path.parent / "subtitle.srt"
    assert result.returncode == 0
    assert result.stdout == f"subtitle: {subtitle_path.resolve()}\n"
    assert result.stderr == ""
    assert (
        subtitle_path.read_bytes()
        == (
            "\ufeff1\n"
            "00:00:00,000 --> 00:00:02,000\n"
            "修正 字幕\n\n"
            "2\n"
            "00:00:02,000 --> 100:01:02,000\n"
            "Second line --> x\n"
        ).encode()
    )
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["status"] == "editable"
    assert job["changed_segment_ids"] == [0, 1]
    assert (
        job["artifacts"]["normalized_transcript_sha256"]
        == hashlib.sha256(normalized_path.read_bytes()).hexdigest()
    )
    assert (
        job["artifacts"]["before_correction_sha256"]
        == hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    )
    assert job["artifacts"]["subtitle"] == str(subtitle_path.resolve())
    assert (
        job["artifacts"]["subtitle_sha256"]
        == hashlib.sha256(subtitle_path.read_bytes()).hexdigest()
    )


def test_finalize_reuses_unchanged_valid_srt_without_audio_or_rewriting(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, _normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    assert run_finalize(job_path.resolve()).returncode == 0
    job = json.loads(job_path.read_text(encoding="utf-8"))
    Path(job["audio"]["path"]).unlink()
    job_bytes = job_path.read_bytes()
    subtitle_path = Path(job["artifacts"]["subtitle"])
    subtitle_bytes = subtitle_path.read_bytes()

    reused = run_finalize(job_path.resolve())

    assert reused.returncode == 0
    assert reused.stdout == f"subtitle: {subtitle_path}\n"
    assert job_path.read_bytes() == job_bytes
    assert subtitle_path.read_bytes() == subtitle_bytes


def test_finalize_rebuilds_after_editing_finalized_transcript(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    assert run_finalize(job_path.resolve()).returncode == 0
    first_job = json.loads(job_path.read_text(encoding="utf-8"))
    first_digest = first_job["artifacts"]["normalized_transcript_sha256"]

    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    normalized["segments"][0]["text"] = "final correction"
    normalized_path.write_bytes(json_bytes(normalized))

    rebuilt = run_finalize(job_path.resolve())

    assert rebuilt.returncode == 0
    assert b"final correction" in (job_path.parent / "subtitle.srt").read_bytes()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["status"] == "editable"
    assert job["changed_segment_ids"] == [0]
    assert job["artifacts"]["normalized_transcript_sha256"] != first_digest


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("metadata", "en"),
        ("segment_count", None),
        ("segment_id", 9),
        ("start", 0.1),
    ],
)
def test_finalize_rejects_changes_outside_segment_text(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
    target: str,
    value: object,
) -> None:
    job_path, normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    if target == "metadata":
        normalized["language"] = value
    elif target == "segment_count":
        normalized["segments"].pop()
    elif target == "segment_id":
        normalized["segments"][0]["id"] = value
    else:
        normalized["segments"][0]["start"] = value
    normalized_path.write_bytes(json_bytes(normalized))

    result = run_finalize(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "editable"
    assert not (job_path.parent / "subtitle.srt").exists()


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        (0.0004, 0.00049, "text"),
        (-0.1, 1, "text"),
        (float("nan"), 1, "text"),
        (True, 1, "text"),
        (0, 1, " \t\n"),
    ],
)
def test_finalize_rejects_invalid_srt_segment(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
    start: object,
    end: object,
    text: str,
) -> None:
    job_path, normalized_path, baseline_path, _manifest_path, _manifest = correction_job
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    baseline["segments"][0].update(start=start, end=end, text=text)
    normalized["segments"][0].update(start=start, end=end, text=text)
    baseline_path.write_bytes(json_bytes(baseline))
    normalized_path.write_bytes(json_bytes(normalized))
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["artifacts"]["before_correction_sha256"] = hashlib.sha256(
        baseline_path.read_bytes()
    ).hexdigest()
    job_path.write_bytes(json_bytes(job))

    result = run_finalize(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "editable"


def test_finalize_rejects_overlap_created_by_millisecond_rounding(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, normalized_path, baseline_path, _manifest_path, _manifest = correction_job
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    baseline["segments"][1]["start"] = 1.9994
    normalized["segments"][1]["start"] = 1.9994
    baseline_path.write_bytes(json_bytes(baseline))
    normalized_path.write_bytes(json_bytes(normalized))
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["artifacts"]["before_correction_sha256"] = hashlib.sha256(
        baseline_path.read_bytes()
    ).hexdigest()
    job_path.write_bytes(json_bytes(job))

    result = run_finalize(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "editable"


def test_finalize_rejects_baseline_digest_mismatch(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, _normalized_path, baseline_path, _manifest_path, _manifest = correction_job
    baseline_path.write_text("changed", encoding="utf-8")

    result = run_finalize(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "editable"


def test_finalize_rebuilds_damaged_srt(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, _normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    assert run_finalize(job_path.resolve()).returncode == 0
    job = json.loads(job_path.read_text(encoding="utf-8"))
    subtitle_path = Path(job["artifacts"]["subtitle"])
    subtitle_path.write_bytes(b"tampered")
    result = run_finalize(job_path.resolve())

    assert result.returncode == 0
    assert result.stdout == f"subtitle: {subtitle_path.resolve()}\n"
    assert subtitle_path.read_bytes() != b"tampered"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["status"] == "editable"
    assert (
        job["artifacts"]["subtitle_sha256"]
        == hashlib.sha256(subtitle_path.read_bytes()).hexdigest()
    )


def test_finalize_publishes_job_last(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import finalize_subtitle

    job_path, _normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    original_write = finalize_subtitle.atomic_write_json

    def fail_job_write(path: Path, value: dict[str, Any]) -> None:
        if path == job_path:
            raise OSError("fail publish")
        original_write(path, value)

    monkeypatch.setattr(finalize_subtitle, "atomic_write_json", fail_job_write)
    with pytest.raises(OSError, match="fail publish"):
        finalize_subtitle.finalize_subtitle(job_path.resolve())

    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "editable"
    assert (job_path.parent / "subtitle.srt").exists()


def test_attach_editable_returns_normalized_and_rejects_different_variant(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, _normalized_path, _baseline_path, manifest_path, manifest = correction_job
    assert run_finalize(job_path.resolve()).returncode == 0
    normalized_path = job_path.parent / "normalized_transcript.json"

    reused = run_attach(job_path.resolve(), manifest_path.resolve())
    assert reused.returncode == 0
    assert reused.stdout == f"normalized_transcript: {normalized_path.resolve()}\n"

    transcript_path = manifest_path.parent / "transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript["variant_id"] = "c" * 64
    transcript_path.write_bytes(json_bytes(transcript))
    manifest["request"]["variant_id"] = "c" * 64
    manifest["artifact_sha256"]["transcript"] = hashlib.sha256(
        transcript_path.read_bytes()
    ).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))

    rejected = run_attach(job_path.resolve(), manifest_path.resolve())
    assert rejected.returncode == 1
    assert rejected.stdout == ""
