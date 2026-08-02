import hashlib
import json
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_transcription(root: Path, audio_path: Path) -> Path:
    output_dir = root / "transcription"
    output_dir.mkdir()
    workspace_dir = output_dir / "workspace"
    workspace_dir.mkdir()
    (output_dir / "transcription.log").write_text("complete\n", encoding="utf-8")
    request = {"provider": "faster-whisper", "language": "zh"}
    variant_id = hashlib.sha256(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    audio_id = _sha256(audio_path)
    transcript_path = output_dir / "transcript.json"
    write_json(
        transcript_path,
        {
            "schema_version": 1,
            "audio_id": audio_id,
            "variant_id": variant_id,
            "provider": "faster-whisper",
            "language": "zh",
            "duration": 12.0,
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": " 转写文本 "},
                {"id": 1, "start": 1.5, "end": 2.0, "text": "结束。"},
            ],
        },
    )
    raw_path = output_dir / "raw_timestamps.json"
    write_json(
        raw_path,
        {
            "schema_version": 1,
            "audio_id": audio_id,
            "variant_id": variant_id,
            "provider": "faster-whisper",
            "language": "zh",
            "duration": 12.0,
            "items": [
                {"text": "转写文本", "start": 0.0, "end": 2.0, "probability": 0.9}
            ],
        },
    )
    manifest_path = output_dir / "result_manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "status": "complete",
            "audio": {
                "id": audio_id,
                "size": audio_path.stat().st_size,
                "sample_count": 12,
                "sample_rate": 1,
                "duration": 12.0,
            },
            "request": {"variant_id": variant_id, **request},
            "artifacts": {
                "transcript": "transcript.json",
                "raw_timestamps": "raw_timestamps.json",
                "log": "transcription.log",
                "workspace": "workspace",
            },
            "artifact_sha256": {
                "transcript": _sha256(transcript_path),
                "raw_timestamps": _sha256(raw_path),
            },
        },
    )
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
    markdown_path = job_path.parent / "transcript.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "title: 测试视频" in markdown
    assert "bvid: BVTEST" in markdown
    assert "url: https://www.bilibili.com/video/BVTEST/" in markdown
    assert "uploader: 测试作者" in markdown
    assert "duration: 12.0" in markdown
    assert "source: audio_transcribe" in markdown
    assert "language: zh" in markdown
    assert "[00:00:00 - 00:00:02] 转写文本 结束。" in markdown
    prompt_path = job_path.parent / job["prompt"]["path"]
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "[Read transcript data](transcript.md)" in prompt
    assert str(job_path.resolve()).replace("\\", "/") not in prompt
    assert str(manifest_path.resolve()).replace("\\", "/") not in prompt
    assert (
        str((manifest_path.parent / "transcript.json").resolve()).replace("\\", "/")
        not in prompt
    )
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

    with pytest.raises(TranscriptionValidationError, match="must be a file"):
        continue_summary.continue_summary(job_path, manifest_path.resolve())


@pytest.mark.parametrize("failure", ["contract", "audio_identity"])
def test_continue_validation_failure_preserves_job_and_local_artifacts(
    workspace_tmp_path: Path,
    failure: str,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    if failure == "audio_identity":
        other_audio = workspace_tmp_path / "other.m4a"
        other_audio.write_bytes(b"other audio")
        manifest_path = make_transcription(workspace_tmp_path, other_audio)
    else:
        manifest_path = make_transcription(workspace_tmp_path, audio_path)
        manifest = read_json(manifest_path)
        manifest["status"] = "incomplete"
        write_json(manifest_path, manifest)
    markdown_path = job_path.parent / "transcript.md"
    prompt_path = job_path.parent / "BVTEST_summary_prompt.md"
    markdown_path.write_text("keep markdown", encoding="utf-8")
    prompt_path.write_text("keep prompt", encoding="utf-8")
    before = read_json(job_path)

    with pytest.raises(TranscriptionValidationError):
        continue_summary.continue_summary(job_path, manifest_path.resolve())

    assert read_json(job_path) == before
    assert markdown_path.read_text(encoding="utf-8") == "keep markdown"
    assert prompt_path.read_text(encoding="utf-8") == "keep prompt"


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


def test_continue_rebuilds_missing_markdown_from_same_manifest(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    job = continue_summary.continue_summary(job_path, manifest_path.resolve())
    transcript_path = manifest_path.parent / "transcript.json"
    original_transcript = transcript_path.read_bytes()
    markdown_path = job_path.parent / "transcript.md"
    markdown_path.unlink()

    restored = continue_summary.continue_summary(job_path, manifest_path.resolve())

    assert restored == job
    assert markdown_path.is_file()
    assert transcript_path.read_bytes() == original_transcript
