from pathlib import Path

import pytest

import scripts.continue_summary as continue_summary
from scripts.transcription_input import TranscriptionInput, TranscriptionInputError
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
            "schema_version": 2,
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
            "prompt": None,
            "error": None,
        },
    )
    return job_path, audio_path


def transcription(audio_id: str) -> TranscriptionInput:
    return TranscriptionInput(
        audio_id=audio_id,
        language="zh",
        duration=12.0,
        segments=[
            {"id": 0, "start": 0.0, "end": 1.0, "text": "转写文本"},
            {"id": 1, "start": 1.5, "end": 2.0, "text": "结束。"},
        ],
    )


def test_continue_imports_job_local_snapshot(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    expected = transcription(continue_summary._file_sha256(audio_path))
    manifest_path = (workspace_tmp_path / "upstream" / "manifest.json").resolve()
    manifest_path.parent.mkdir()
    manifest_path.write_text("opaque", encoding="utf-8")
    monkeypatch.setattr(continue_summary, "load_transcription", lambda _path: expected)

    job = continue_summary.continue_summary(job_path, manifest_path)

    assert job["status"] == "prompt_ready"
    assert job["transcript"] == {"source": "audio_transcribe", "path": "transcript.md"}
    assert "transcription_manifest" not in job
    markdown = (job_path.parent / "transcript.md").read_text(encoding="utf-8")
    assert "[00:00:00 - 00:00:02] 转写文本 结束。" in markdown
    prompt = (job_path.parent / job["prompt"]["path"]).read_text(encoding="utf-8")
    assert "[Read transcript data](transcript.md)" in prompt


def test_continue_requires_absolute_manifest(workspace_tmp_path: Path) -> None:
    job_path, _ = make_needs_job(workspace_tmp_path)
    with pytest.raises(TranscriptionInputError, match="absolute"):
        continue_summary.continue_summary(job_path, Path("manifest.json"))


@pytest.mark.parametrize("failure", ["contract", "audio_identity"])
def test_continue_failure_preserves_job_and_local_artifacts(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = (workspace_tmp_path / "manifest.json").resolve()
    manifest_path.write_text("opaque", encoding="utf-8")
    if failure == "contract":

        def fail(_path: Path) -> TranscriptionInput:
            raise TranscriptionInputError("invalid transcription")

        monkeypatch.setattr(continue_summary, "load_transcription", fail)
    else:
        monkeypatch.setattr(
            continue_summary,
            "load_transcription",
            lambda _path: transcription("a" * 64),
        )
    markdown_path = job_path.parent / "transcript.md"
    prompt_path = job_path.parent / "BVTEST_summary_prompt.md"
    markdown_path.write_text("keep markdown", encoding="utf-8")
    prompt_path.write_text("keep prompt", encoding="utf-8")
    before = read_json(job_path)

    with pytest.raises(TranscriptionInputError):
        continue_summary.continue_summary(job_path, manifest_path)

    assert read_json(job_path) == before
    assert markdown_path.read_text(encoding="utf-8") == "keep markdown"
    assert prompt_path.read_text(encoding="utf-8") == "keep prompt"
    assert audio_path.is_file()


def test_continue_rejects_job_local_path_escape(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, _ = make_needs_job(workspace_tmp_path)
    job = read_json(job_path)
    job["resources"]["audio"] = "../outside.m4a"
    write_json(job_path, job)
    monkeypatch.setattr(
        continue_summary, "load_transcription", lambda _path: transcription("a" * 64)
    )

    with pytest.raises(ValueError, match="escapes"):
        continue_summary.continue_summary(job_path, workspace_tmp_path.resolve())


def test_continue_reuses_local_snapshot_without_reading_upstream(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = (workspace_tmp_path / "manifest.json").resolve()
    manifest_path.write_text("opaque", encoding="utf-8")
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: transcription(continue_summary._file_sha256(audio_path)),
    )
    first = continue_summary.continue_summary(job_path, manifest_path)

    def unexpected(_path: Path) -> TranscriptionInput:
        raise AssertionError("published jobs must not reload upstream transcription")

    monkeypatch.setattr(continue_summary, "load_transcription", unexpected)
    other_manifest = (workspace_tmp_path / "other" / "manifest.json").resolve()

    assert continue_summary.continue_summary(job_path, other_manifest) == first


def test_continue_main_moves_success_log_and_keeps_stdout_contract(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = (workspace_tmp_path / "manifest.json").resolve()
    manifest_path.write_text("opaque", encoding="utf-8")
    monkeypatch.setattr(continue_summary, "SKILL_ROOT", workspace_tmp_path)
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: transcription(continue_summary._file_sha256(audio_path)),
    )

    assert (
        continue_summary.main(
            [str(job_path), "--transcription-manifest", str(manifest_path)]
        )
        == 0
    )

    terminal = capsys.readouterr()
    assert terminal.out == f"Summary job is prompt_ready: {job_path.resolve()}\n"
    assert terminal.err == ""
    assert len(list(job_path.parent.glob("continue-*.log"))) == 1
    assert not list((workspace_tmp_path / ".cache" / "logs").glob("continue-*.log"))


def test_continue_main_keeps_failure_log_in_cache(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    job_path, _audio_path = make_needs_job(workspace_tmp_path)
    manifest_path = (workspace_tmp_path / "manifest.json").resolve()
    manifest_path.write_text("opaque", encoding="utf-8")
    monkeypatch.setattr(continue_summary, "SKILL_ROOT", workspace_tmp_path)
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: transcription("a" * 64),
    )

    assert (
        continue_summary.main(
            [str(job_path), "--transcription-manifest", str(manifest_path)]
        )
        == 1
    )

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert "Error: transcription result audio does not match" in terminal.err
    logs = list((workspace_tmp_path / ".cache" / "logs").glob("continue-*.log"))
    assert len(logs) == 1
    assert "Traceback (most recent call last)" in logs[0].read_text(encoding="utf-8")
