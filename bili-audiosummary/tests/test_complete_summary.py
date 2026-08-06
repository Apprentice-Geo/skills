from pathlib import Path

import pytest

import scripts.complete_summary as complete_summary
import scripts.continue_summary as continue_summary
from scripts.summary_job import TranscriptionValidationError
from scripts.utils import read_json, write_json
from tests.test_continue_summary import make_needs_job, make_transcription


def test_complete_keeps_prompt_ready_when_summary_is_invalid(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    job = continue_summary.continue_summary(job_path, manifest_path.resolve())
    summary_path = job_path.parent / job["prompt"]["summary_path"]
    summary_path.write_text("{{placeholder}}", encoding="utf-8")

    current, result = complete_summary.complete_summary(job_path)

    assert not result.ok
    assert current["status"] == "prompt_ready"
    assert read_json(job_path)["status"] == "prompt_ready"


def test_complete_accepts_warning_and_is_idempotent(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    job = continue_summary.continue_summary(job_path, manifest_path.resolve())
    summary_path = job_path.parent / job["prompt"]["summary_path"]
    summary_path.write_text("# Summary\n\nEnglish only text.\n", encoding="utf-8")
    (manifest_path.parent / "transcript.json").unlink()

    completed, result = complete_summary.complete_summary(job_path)
    repeated, repeated_result = complete_summary.complete_summary(job_path)

    assert result.ok and result.warnings
    assert completed["status"] == "complete"
    assert repeated == completed
    assert repeated_result.ok


def test_continue_rebuilds_missing_prompt_before_completion(
    workspace_tmp_path: Path,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = make_transcription(workspace_tmp_path, audio_path)
    job = continue_summary.continue_summary(job_path, manifest_path.resolve())
    prompt_path = job_path.parent / job["prompt"]["path"]
    prompt_path.unlink()
    (manifest_path.parent / "transcript.json").unlink()

    with pytest.raises(TranscriptionValidationError):
        continue_summary.continue_summary(job_path, manifest_path.resolve())
    assert read_json(job_path)["status"] == "prompt_ready"
    assert not prompt_path.is_file()


@pytest.mark.parametrize(
    "segments",
    [
        [None],
        [{"id": 1, "start": 0.0, "end": 1.0, "text": "bad"}],
        [{"id": 0, "start": 1.0, "end": 1.0, "text": "bad"}],
        [{"id": 0, "start": 1.0, "end": 0.5, "text": "bad"}],
        [{"id": 0, "start": 0.0, "end": 1.0, "text": "  "}],
    ],
)
def test_complete_rejects_malformed_native_subtitle_transcript(
    workspace_tmp_path: Path,
    segments,
) -> None:
    job_path, _audio_path = make_needs_job(workspace_tmp_path)
    job = read_json(job_path)
    transcript_path = job_path.parent / "BVTEST_transcript.json"
    write_json(
        transcript_path,
        {
            "bvid": "BVTEST",
            "source": "subtitle",
            "segments": segments,
        },
    )
    prompt_path = job_path.parent / "BVTEST_summary_prompt.md"
    summary_path = job_path.parent / "BVTEST_summary_zh.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    summary_path.write_text("# 总结\n\n内容。\n", encoding="utf-8")
    job.update(
        {
            "status": "prompt_ready",
            "transcript": {
                "source": "bilibili_subtitle",
                "path": transcript_path.name,
            },
            "prompt": {
                "path": prompt_path.name,
                "summary_path": summary_path.name,
            },
        }
    )
    write_json(job_path, job)

    with pytest.raises(ValueError, match="subtitle transcript"):
        complete_summary.complete_summary(job_path)
