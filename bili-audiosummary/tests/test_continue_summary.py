import hashlib
import json
from pathlib import Path

import pytest

import scripts.continue_summary as continue_summary
from scripts.summary_job import TranscriptionValidationError
from scripts.utils import read_json, write_json


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def make_transcription(root: Path, audio_path: Path) -> Path:
    output_dir = root / "transcription"
    output_dir.mkdir()
    audio_id = digest(audio_path)
    request = {
        "provider": "faster-whisper",
        "language": "zh",
        "provider_identity": {
            "model": {
                "logical_id": "faster-whisper-small",
                "repo": "Systran/faster-whisper-small",
                "revision": "536b0662742c02347bc0e980a01041f333bce120",
            }
        },
        "execution_policy": {"policy": "whisper-cpu"},
        "vad_parameters": {"schema_version": 1},
        "planning_parameters": {"schema_version": 1},
        "segmentation_schema_version": 1,
    }
    variant_id = hashlib.sha256(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    transcript_path = output_dir / "transcript.json"
    timestamps_path = output_dir / "raw_timestamps.json"
    (output_dir / "workspace").mkdir()
    (output_dir / "transcribe.log").write_text("test\n", encoding="utf-8")
    write_json(
        transcript_path,
        {
            "schema_version": 1,
            "audio_id": audio_id,
            "variant_id": variant_id,
            "provider": "faster-whisper",
            "language": "zh",
            "duration": 1.0,
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "转写文本。"}],
        },
    )
    write_json(
        timestamps_path,
        {
            "schema_version": 1,
            "audio_id": audio_id,
            "variant_id": variant_id,
            "provider": "faster-whisper",
            "language": "zh",
            "duration": 1.0,
            "items": [{"text": "转写", "start": 0.0, "end": 1.0, "probability": 0.9}],
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
                "sample_count": 16_000,
                "sample_rate": 16_000,
                "duration": 1.0,
            },
            "request": {"variant_id": variant_id, **request},
            "artifacts": {
                "transcript": "transcript.json",
                "raw_timestamps": "raw_timestamps.json",
                "log": "transcribe.log",
                "workspace": "workspace",
            },
            "artifact_sha256": {
                "transcript": digest(transcript_path),
                "raw_timestamps": digest(timestamps_path),
            },
        },
    )
    return manifest_path


def test_continue_validates_external_result_and_publishes_prompt(
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


def test_continue_rejects_variant_id_not_derived_from_request(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    manifest = read_json(manifest_path)
    manifest["request"]["variant_id"] = "a" * 64
    write_json(manifest_path, manifest)

    with pytest.raises(
        TranscriptionValidationError,
        match="variant_id does not match request",
    ):
        continue_summary.continue_summary(job_path, manifest_path.resolve())


def test_continue_rejects_qwen_probability_field(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    manifest = read_json(manifest_path)
    request = manifest["request"]
    request["provider"] = "qwen3"
    request["provider_identity"] = {"model": {"logical_id": "qwen3-asr-0.6b"}}
    canonical_request = {
        key: value for key, value in request.items() if key != "variant_id"
    }
    variant_id = hashlib.sha256(
        json.dumps(
            canonical_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    request["variant_id"] = variant_id
    for artifact_name in ("transcript", "raw_timestamps"):
        artifact_path = manifest_path.parent / f"{artifact_name}.json"
        artifact = read_json(artifact_path)
        artifact["provider"] = "qwen3"
        artifact["variant_id"] = variant_id
        write_json(artifact_path, artifact)
        manifest["artifact_sha256"][artifact_name] = digest(artifact_path)
    write_json(manifest_path, manifest)

    with pytest.raises(TranscriptionValidationError, match="probability"):
        continue_summary.continue_summary(job_path, manifest_path.resolve())


def test_repeated_continue_rejects_tampered_transcript_binding_and_rolls_back(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    job = continue_summary.continue_summary(job_path, manifest_path.resolve())
    unrelated = job_path.parent / "notes.json"
    unrelated.write_text("{}", encoding="utf-8")
    job["transcript"]["path"] = str(unrelated.resolve())
    write_json(job_path, job)

    with pytest.raises(
        TranscriptionValidationError,
        match="transcript path does not match",
    ):
        continue_summary.continue_summary(job_path, manifest_path.resolve())

    assert read_json(job_path)["status"] == "needs_transcription"


def test_continue_accepts_new_manifest_after_old_binding_becomes_invalid(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    (workspace_tmp_path / "old").mkdir()
    old_manifest = make_transcription(workspace_tmp_path / "old", audio_path)
    continue_summary.continue_summary(job_path, old_manifest.resolve())
    (old_manifest.parent / "raw_timestamps.json").write_text(
        "tampered", encoding="utf-8"
    )
    (workspace_tmp_path / "new").mkdir()
    new_manifest = make_transcription(workspace_tmp_path / "new", audio_path)

    updated = continue_summary.continue_summary(job_path, new_manifest.resolve())

    assert updated["status"] == "prompt_ready"
    assert updated["transcription_manifest"] == str(new_manifest.resolve())


def test_continue_requires_absolute_manifest(workspace_tmp_path: Path) -> None:
    job_path, _ = make_needs_job(workspace_tmp_path)
    with pytest.raises(TranscriptionValidationError, match="absolute"):
        continue_summary.continue_summary(job_path, Path("result_manifest.json"))


@pytest.mark.parametrize(
    "tamper",
    ["audio", "digest", "path_escape", "segment_id", "language", "raw_schema"],
)
def test_continue_rejects_invalid_external_identity_or_artifact(
    workspace_tmp_path: Path,
    tamper: str,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    manifest = read_json(manifest_path)
    if tamper == "audio":
        manifest["audio"]["id"] = "0" * 64
    elif tamper == "digest":
        manifest["artifact_sha256"]["transcript"] = "0" * 64
    elif tamper == "path_escape":
        manifest["artifacts"]["log"] = "../transcribe.log"
    elif tamper in {"segment_id", "language"}:
        transcript_path = manifest_path.parent / "transcript.json"
        transcript = read_json(transcript_path)
        if tamper == "segment_id":
            transcript["segments"][0]["id"] = 2
        else:
            transcript["language"] = "en"
        write_json(transcript_path, transcript)
        manifest["artifact_sha256"]["transcript"] = digest(transcript_path)
    else:
        timestamps_path = manifest_path.parent / "raw_timestamps.json"
        timestamps = read_json(timestamps_path)
        timestamps["schema_version"] = 2
        write_json(timestamps_path, timestamps)
        manifest["artifact_sha256"]["raw_timestamps"] = digest(timestamps_path)
    write_json(manifest_path, manifest)

    with pytest.raises(TranscriptionValidationError):
        continue_summary.continue_summary(job_path, manifest_path.resolve())

    assert read_json(job_path)["status"] == "needs_transcription"


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


def test_invalidated_external_result_rolls_ready_job_back_without_touching_summary(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    ready = continue_summary.continue_summary(job_path, manifest_path.resolve())
    summary_path = job_path.parent / ready["prompt"]["summary_path"]
    summary_path.write_text("用户已经写出的总结", encoding="utf-8")
    (manifest_path.parent / "transcript.json").unlink()

    with pytest.raises(TranscriptionValidationError):
        continue_summary.continue_summary(job_path, manifest_path.resolve())

    rolled_back = read_json(job_path)
    assert rolled_back["status"] == "needs_transcription"
    assert rolled_back["transcription_manifest"] is None
    assert rolled_back["prompt"] is None
    assert summary_path.read_text(encoding="utf-8") == "用户已经写出的总结"
    assert not (job_path.parent / ready["prompt"]["path"]).exists()
