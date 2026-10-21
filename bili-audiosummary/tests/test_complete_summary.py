from pathlib import Path

import pytest

import scripts.complete_summary as complete_summary
import scripts.continue_summary as continue_summary
from scripts.utils import read_json, write_json
from tests.test_continue_summary import make_needs_job, transcription


def import_transcription(
    job_path: Path,
    audio_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict, Path]:
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: transcription(continue_summary._file_sha256(audio_path)),
    )
    manifest_path = (job_path.parent / "upstream" / "manifest.json").resolve()
    manifest_path.parent.mkdir()
    manifest_path.write_text("opaque", encoding="utf-8")
    return continue_summary.continue_summary(job_path, manifest_path), manifest_path


def test_complete_keeps_prompt_ready_when_summary_is_invalid(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    job, _manifest_path = import_transcription(job_path, audio_path, monkeypatch)
    summary_path = job_path.parent / job["prompt"]["summary_path"]
    summary_path.write_text(" \n", encoding="utf-8")

    current, result = complete_summary.complete_summary(job_path)

    assert not result.ok
    assert current["status"] == "prompt_ready"
    assert read_json(job_path)["status"] == "prompt_ready"


def test_complete_accepts_warning_and_is_idempotent(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    job, manifest_path = import_transcription(job_path, audio_path, monkeypatch)
    summary_path = job_path.parent / job["prompt"]["summary_path"]
    summary_path.write_text(
        "English only text, without fixed headings.",
        encoding="utf-8",
    )
    manifest_path.unlink()

    completed, result = complete_summary.complete_summary(job_path)
    repeated, repeated_result = complete_summary.complete_summary(job_path)

    assert result.ok and result.warnings
    assert completed["status"] == "complete"
    assert repeated == completed
    assert repeated_result.ok


def test_continue_restores_missing_prompt_without_upstream(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    job, manifest_path = import_transcription(job_path, audio_path, monkeypatch)
    prompt_path = job_path.parent / job["prompt"]["path"]
    prompt_path.unlink()
    manifest_path.unlink()

    assert continue_summary.continue_summary(job_path) == job
    assert read_json(job_path)["status"] == "prompt_ready"
    assert prompt_path.is_file()


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
            "status": "complete",
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
    assert read_json(job_path)["status"] == "prompt_ready"


def test_complete_main_moves_success_log_and_routes_warning_to_stderr(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    job, _manifest_path = import_transcription(job_path, audio_path, monkeypatch)
    summary_path = job_path.parent / job["prompt"]["summary_path"]
    summary_path.write_text(
        "English only text, without fixed headings.",
        encoding="utf-8",
    )
    monkeypatch.setattr(complete_summary, "SKILL_ROOT", workspace_tmp_path)

    assert complete_summary.main([str(job_path)]) == 0

    terminal = capsys.readouterr()
    assert terminal.out == (
        "Summary validation passed with warnings:\n"
        f"Summary job is complete: {job_path.resolve()}\n"
    )
    assert terminal.err.startswith("- summary language does not match")
    assert (
        len(list((workspace_tmp_path / ".cache" / "logs").glob("complete-*.log"))) == 1
    )


def test_complete_main_keeps_validation_failure_log_in_cache(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    job, _manifest_path = import_transcription(job_path, audio_path, monkeypatch)
    summary_path = job_path.parent / job["prompt"]["summary_path"]
    summary_path.write_text(" \n", encoding="utf-8")
    monkeypatch.setattr(complete_summary, "SKILL_ROOT", workspace_tmp_path)

    assert complete_summary.main([str(job_path)]) == 1

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err.startswith("Summary validation failed:\n")
    assert (
        len(list((workspace_tmp_path / ".cache" / "logs").glob("complete-*.log"))) == 1
    )
    assert not list(job_path.parent.glob("complete-*.log"))


@pytest.mark.parametrize("missing", ["source", "prompt"])
def test_complete_reopens_finished_job_when_required_artifact_is_missing(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    job, _ = import_transcription(job_path, audio_path, monkeypatch)
    from tests.test_validate_summary import valid_summary

    summary_path = job_path.parent / job["prompt"]["summary_path"]
    summary_path.write_text(
        valid_summary("zh", "转写支持的完整总结。"), encoding="utf-8"
    )
    complete_summary.complete_summary(job_path)
    artifact = (
        job_path.parent / job["transcript"]["path"]
        if missing == "source"
        else job_path.parent / job["prompt"]["path"]
    )
    artifact.unlink()

    with pytest.raises(ValueError, match="does not exist"):
        complete_summary.complete_summary(job_path)

    assert read_json(job_path)["status"] == "prompt_ready"
    if missing == "prompt":
        continue_summary.continue_summary(job_path)
        completed, validation = complete_summary.complete_summary(job_path)
        assert validation.ok and completed["status"] == "complete"
