from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from scripts import generate_srt, subtitle_job

SKILL_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = SKILL_DIR / "results"


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def run_generate(job_path: Path | str) -> subprocess.CompletedProcess[str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        returncode = generate_srt.main([str(job_path)])
    return subprocess.CompletedProcess(
        args=[str(job_path)],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


@pytest.fixture
def correction_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, Path, Path, Path, dict[str, Any]]]:
    audio_path = tmp_path / "sample.wav"
    audio_content = b"finalize audio"
    audio_path.write_bytes(audio_content)
    audio_id = hashlib.sha256(audio_content).hexdigest()
    results_dir = tmp_path / "results"
    monkeypatch.setenv("SUBTITLE_CREATOR_DATA_DIR", str(results_dir.parent))
    monkeypatch.setitem(globals(), "RESULTS_DIR", results_dir)
    job_dir = RESULTS_DIR / audio_id
    job_dir.mkdir(parents=True)

    manifest_path = tmp_path / "upstream" / "manifest.json"
    manifest = {"opaque": True}

    baseline = {
        "source": "audio_transcribe",
        "provider": "faster-whisper",
        "language": "zh",
        "duration": 360062,
        "items": [
            {"text": "第一段", "start": 0.0004, "end": 1.9995, "probability": None},
            {"text": "Second line --> x", "start": 1.9995, "end": 360061.9995, "probability": None},
        ],
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
        "job_id": audio_id,
        "status": "transcription_bound",
        "audio": {"path": str(audio_path.resolve()), "id": audio_id},
        "artifacts": {
            "normalized_transcript": str(normalized_path.resolve()),
            "before_correction": str(baseline_path.resolve()),
            "before_correction_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "subtitle": None,
        },
        "changed_segment_ids": [],
        "subtitle_options": None,
    }
    job_path.write_bytes(json_bytes(job))
    yield job_path, normalized_path, baseline_path, manifest_path, manifest
    shutil.rmtree(job_dir, ignore_errors=True)


def test_generate_publishes_corrected_srt_and_keeps_transcription_bound_job(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, normalized_path, baseline_path, _manifest_path, _manifest = correction_job
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    normalized["segments"][0]["text"] = "  修正\n字幕  "
    normalized["segments"][1]["text"] = " Second\t line --> x "
    normalized_path.write_bytes(json_bytes(normalized))

    result = run_generate(job_path.resolve())

    subtitle_path = job_path.parent / "subtitle.srt"
    assert result.returncode == 0
    assert result.stdout == f"subtitle: {subtitle_path.resolve()}\n"
    assert "cannot subdivide segment IDs [0]" in result.stderr
    logs = list((job_path.parents[2] / ".cache" / "logs").glob("generate-srt-*.log"))
    assert len(logs) == 1
    assert result.stdout.strip() in logs[0].read_text(encoding="utf-8")
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
    assert job["status"] == "transcription_bound"
    assert job["changed_segment_ids"] == [0, 1]
    assert (
        job["artifacts"]["before_correction_sha256"]
        == hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    )
    assert job["artifacts"]["subtitle"] == str(subtitle_path.resolve())


def test_generate_always_rewrites_unchanged_srt_without_audio(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_path, normalized_path, baseline_path, _manifest_path, _manifest = correction_job
    assert run_generate(job_path.resolve()).returncode == 0
    job = json.loads(job_path.read_text(encoding="utf-8"))
    Path(job["audio"]["path"]).unlink()
    job_bytes = job_path.read_bytes()
    subtitle_path = Path(job["artifacts"]["subtitle"])
    subtitle_bytes = subtitle_path.read_bytes()

    writes = []
    reads = []
    renders = []
    original_write = generate_srt._atomic_write
    original_read = subtitle_job.read_json_object
    original_render = subtitle_job.rendered_srt_segments

    def record_write(path, content):
        writes.append(path)
        original_write(path, content)

    def record_read(path, **kwargs):
        reads.append(path)
        return original_read(path, **kwargs)

    def record_render(*args, **kwargs):
        renders.append(True)
        return original_render(*args, **kwargs)

    monkeypatch.setattr(generate_srt, "_atomic_write", record_write)
    monkeypatch.setattr(subtitle_job, "read_json_object", record_read)
    monkeypatch.setattr(subtitle_job, "rendered_srt_segments", record_render)
    reused = run_generate(job_path.resolve())
    assert writes == [subtitle_path]
    assert reads.count(baseline_path) == 1
    assert reads.count(normalized_path) == 1
    assert len(renders) == 1

    assert reused.returncode == 0
    assert reused.stdout == f"subtitle: {subtitle_path}\n"
    assert job_path.read_bytes() == job_bytes
    assert subtitle_path.read_bytes() == subtitle_bytes


def test_generate_rebuilds_after_editing_finalized_transcript(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    assert run_generate(job_path.resolve()).returncode == 0
    first_subtitle = (job_path.parent / "subtitle.srt").read_bytes()

    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    normalized["segments"][0]["text"] = "final correction"
    normalized_path.write_bytes(json_bytes(normalized))

    rebuilt = run_generate(job_path.resolve())

    assert rebuilt.returncode == 0
    assert b"final correction" in (job_path.parent / "subtitle.srt").read_bytes()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["status"] == "transcription_bound"
    assert job["changed_segment_ids"] == [0]
    assert (job_path.parent / "subtitle.srt").read_bytes() != first_subtitle


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("metadata", "en"),
        ("segment_count", None),
        ("segment_id", 9),
        ("start", 0.1),
    ],
)
def test_generate_rejects_changes_outside_segment_text(
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

    result = run_generate(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "transcription_bound"
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
def test_generate_rejects_invalid_srt_segment(
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

    result = run_generate(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "transcription_bound"


def test_generate_rejects_overlap_created_by_millisecond_rounding(
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

    result = run_generate(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "transcription_bound"


def test_generate_rejects_baseline_digest_mismatch(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, _normalized_path, baseline_path, _manifest_path, _manifest = correction_job
    baseline_path.write_text("changed", encoding="utf-8")

    result = run_generate(job_path.resolve())

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("Error: ")
    assert "Full log:" in result.stderr
    logs = list((job_path.parents[2] / ".cache" / "logs").glob("generate-srt-*.log"))
    assert len(logs) == 1
    assert "Traceback (most recent call last)" in logs[0].read_text(encoding="utf-8")
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "transcription_bound"


def test_generate_rebuilds_damaged_srt(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
) -> None:
    job_path, _normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    assert run_generate(job_path.resolve()).returncode == 0
    job = json.loads(job_path.read_text(encoding="utf-8"))
    subtitle_path = Path(job["artifacts"]["subtitle"])
    subtitle_path.write_bytes(b"tampered")
    result = run_generate(job_path.resolve())

    assert result.returncode == 0
    assert result.stdout == f"subtitle: {subtitle_path.resolve()}\n"
    assert subtitle_path.read_bytes() != b"tampered"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["status"] == "transcription_bound"


def test_generate_rejects_incorrect_written_subtitle(correction_job, monkeypatch):
    job_path = correction_job[0]
    original_job = job_path.read_bytes()

    def write_incorrect_subtitle(path, _content):
        path.write_bytes(b"incorrect output")

    monkeypatch.setattr(generate_srt, "_atomic_write", write_incorrect_subtitle)

    with pytest.raises(subtitle_job.SubtitleJobError, match="subtitle artifact timeline or text"):
        generate_srt.generate_srt(job_path)

    assert job_path.read_bytes() == original_job


def test_generate_publishes_job_last(
    correction_job: tuple[Path, Path, Path, Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_path, _normalized_path, _baseline_path, _manifest_path, _manifest = correction_job
    original_write = generate_srt.atomic_write_json

    def fail_job_write(path: Path, value: dict[str, Any]) -> None:
        if path == job_path:
            raise OSError("fail publish")
        original_write(path, value)

    monkeypatch.setattr(generate_srt, "atomic_write_json", fail_job_write)
    with pytest.raises(OSError, match="fail publish"):
        generate_srt.generate_srt(job_path.resolve())

    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "transcription_bound"
    assert (job_path.parent / "subtitle.srt").exists()
