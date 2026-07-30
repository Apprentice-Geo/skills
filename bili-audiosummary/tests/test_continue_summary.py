from pathlib import Path

import pytest

import scripts.continue_summary as continue_summary
from scripts.summary_job import TranscriptionValidationError
from scripts.utils import read_json, write_json


def make_needs_job(root: Path) -> tuple[Path, Path]:
    result_dir = root / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    resource_dir.mkdir(parents=True)
    audio_path = resource_dir / "BVTEST.m4a"
    audio_path.write_bytes(b"source audio")
    write_json(resource_dir / "fetch_manifest.json", {"id": "BVTEST"})
    job_path = result_dir / "summary_job.json"
    write_json(
        job_path,
        {
            "schema_version": 1,
            "status": "needs_transcription",
            "video": {
                "bvid": "BVTEST",
                "title": "测试视频",
                "url": "https://www.bilibili.com/video/BVTEST/",
                "uploader": "测试作者",
                "summary_language": None,
            },
            "resources": {
                "fetch_manifest": "resource/fetch_manifest.json",
                "subtitle": None,
                "audio": "resource/BVTEST.m4a",
                "subtitle_skipped": False,
            },
            "transcript": None,
            "transcription_manifest": None,
            "prompt": None,
            "error": None,
        },
    )
    return job_path, audio_path


def make_transcription(root: Path, _audio_path: Path) -> Path:
    output_dir = root / "transcription"
    output_dir.mkdir()
    transcript_path = output_dir / "transcript.json"
    write_json(
        transcript_path,
        {
            "language": "zh",
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "转写文本。"}],
        },
    )
    manifest_path = output_dir / "result_manifest.json"
    write_json(manifest_path, {"artifacts": {"transcript": "transcript.json"}})
    return manifest_path


def test_continue_reads_external_transcript_and_publishes_prompt(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)

    job = continue_summary.continue_summary(job_path, manifest_path.resolve())

    assert job["status"] == "prompt_ready"
    assert job["transcription_manifest"] == str(manifest_path.resolve())
    assert (
        Path(job["transcript"]["path"]).resolve()
        == (manifest_path.parent / "transcript.json").resolve()
    )
    prompt_path = job_path.parent / job["prompt"]["path"]
    prompt = prompt_path.read_text(encoding="utf-8")
    assert str(job_path.resolve()).replace("\\", "/") in prompt
    assert str(manifest_path.resolve()).replace("\\", "/") in prompt
    assert "Read `segments` in order." in prompt
    assert read_json(job_path) == job


def test_continue_requires_absolute_manifest(workspace_tmp_path: Path) -> None:
    job_path, _ = make_needs_job(workspace_tmp_path)
    with pytest.raises(TranscriptionValidationError, match="absolute"):
        continue_summary.continue_summary(job_path, Path("result_manifest.json"))


@pytest.mark.parametrize("artifact_path", ["../transcript.json", "ABSOLUTE"])
def test_continue_rejects_unsafe_transcript_path(
    workspace_tmp_path: Path,
    artifact_path: str,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    manifest = read_json(manifest_path)
    manifest["artifacts"]["transcript"] = (
        str((manifest_path.parent / "transcript.json").resolve())
        if artifact_path == "ABSOLUTE"
        else artifact_path
    )
    write_json(manifest_path, manifest)

    with pytest.raises(TranscriptionValidationError):
        continue_summary.continue_summary(job_path, manifest_path.resolve())

    assert read_json(job_path)["status"] == "needs_transcription"


def test_continue_rejects_missing_transcript(workspace_tmp_path: Path) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    (manifest_path.parent / "transcript.json").unlink()

    with pytest.raises(TranscriptionValidationError, match="does not exist"):
        continue_summary.continue_summary(job_path, manifest_path.resolve())


def test_continue_rejects_job_local_path_escape(workspace_tmp_path: Path) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    job = read_json(job_path)
    job["resources"]["audio"] = "../outside.m4a"
    write_json(job_path, job)

    with pytest.raises(ValueError, match="escapes"):
        continue_summary.continue_summary(job_path, manifest_path.resolve())


def test_continue_is_idempotent_for_same_manifest_and_rejects_different_one(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    first = continue_summary.continue_summary(job_path, manifest_path.resolve())

    assert continue_summary.continue_summary(job_path, manifest_path.resolve()) == first
    other = workspace_tmp_path / "other" / "result_manifest.json"
    other.parent.mkdir()
    other.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="different transcription"):
        continue_summary.continue_summary(job_path, other.resolve())
