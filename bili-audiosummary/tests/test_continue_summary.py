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
    assert "[读取转写数据](transcript.md)" in prompt


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
    summary_path = job_path.parent / "BVTEST_summary_zh.md"
    summary_path.write_text("keep user summary", encoding="utf-8")
    before = read_json(job_path)

    with pytest.raises(TranscriptionInputError):
        continue_summary.continue_summary(job_path, manifest_path)

    assert read_json(job_path) == before
    assert markdown_path.read_text(encoding="utf-8") == "keep markdown"
    assert prompt_path.read_text(encoding="utf-8") == "keep prompt"
    assert summary_path.read_text(encoding="utf-8") == "keep user summary"
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
    assert continue_summary.continue_summary(job_path) == first


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
    assert (
        len(list((workspace_tmp_path / ".cache" / "logs").glob("continue-*.log"))) == 1
    )
    assert not list(job_path.parent.glob("continue-*.log"))


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


def test_explicit_manifest_reimports_and_preserves_previous_summary(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    payload = transcription(continue_summary._file_sha256(audio_path))
    monkeypatch.setattr(continue_summary, "load_transcription", lambda _path: payload)
    manifest = (workspace_tmp_path / "manifest.json").resolve()
    job = continue_summary.continue_summary(job_path, manifest)
    summary = job_path.parent / job["prompt"]["summary_path"]
    summary.write_text("user summary", encoding="utf-8")
    job["status"] = "complete"
    write_json(job_path, job)
    payload.segments[0]["text"] = "新的转写"

    updated = continue_summary.continue_summary(job_path, manifest)

    assert updated["status"] == "prompt_ready"
    assert "新的转写" in (job_path.parent / "transcript.md").read_text(encoding="utf-8")
    assert not summary.exists()
    archived = list(job_path.parent.glob("*.superseded-*.md"))
    assert [path.read_text(encoding="utf-8") for path in archived] == ["user summary"]
    assert audio_path.is_file()


def test_language_change_preserves_transcript_and_archives_both_summary_paths(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_path, audio_path = make_needs_job(workspace_tmp_path)
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: transcription(continue_summary._file_sha256(audio_path)),
    )
    job = continue_summary.continue_summary(
        job_path, (workspace_tmp_path / "manifest.json").resolve()
    )
    transcript_path = job_path.parent / "transcript.md"
    before = transcript_path.read_bytes()
    (job_path.parent / job["prompt"]["summary_path"]).write_text(
        "old zh", encoding="utf-8"
    )
    (job_path.parent / "BVTEST_summary_en.md").write_text(
        "existing en", encoding="utf-8"
    )

    updated = continue_summary.continue_summary(job_path, summary_language="en")

    assert updated["status"] == "prompt_ready"
    assert updated["prompt"]["summary_path"] == "BVTEST_summary_en.md"
    assert transcript_path.read_bytes() == before
    assert not (job_path.parent / "BVTEST_summary_en.md").exists()
    assert sorted(
        path.read_text(encoding="utf-8")
        for path in job_path.parent.glob("*.superseded-*.md")
    ) == ["existing en", "old zh"]


@pytest.mark.parametrize("explicit_language, expected", [(None, "en"), ("zh", "zh")])
def test_reimport_follows_new_transcript_language_unless_explicitly_selected(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_language: str | None,
    expected: str,
) -> None:
    from dataclasses import replace

    job_path, audio_path = make_needs_job(workspace_tmp_path)
    payload = transcription(continue_summary._file_sha256(audio_path))
    monkeypatch.setattr(continue_summary, "load_transcription", lambda _path: payload)
    manifest = (workspace_tmp_path / "manifest.json").resolve()
    continue_summary.continue_summary(
        job_path, manifest, summary_language=explicit_language
    )
    payload = replace(payload, language="en")

    job = continue_summary.continue_summary(job_path, manifest)

    assert job["prompt"]["summary_path"] == f"BVTEST_summary_{expected}.md"
    assert job["video"]["summary_language"] == explicit_language


def completed_job_with_summary(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    job_path, audio = make_needs_job(root)
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: transcription(continue_summary._file_sha256(audio)),
    )
    job = continue_summary.continue_summary(
        job_path, (root / "manifest.json").resolve()
    )
    summary = job_path.parent / job["prompt"]["summary_path"]
    summary.write_text("existing user summary", encoding="utf-8")
    job["status"] = "complete"
    write_json(job_path, job)
    return job_path, summary


def test_failed_invalidation_does_not_move_summary_or_change_input(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_path, summary = completed_job_with_summary(workspace_tmp_path, monkeypatch)
    before = (job_path.parent / "transcript.md").read_bytes()
    monkeypatch.setattr(
        continue_summary,
        "publish_job",
        lambda *_args: (_ for _ in ()).throw(PermissionError("publish denied")),
    )

    with pytest.raises(PermissionError, match="publish denied"):
        continue_summary.continue_summary(job_path, summary_language="en")

    assert read_json(job_path)["status"] == "complete"
    assert summary.read_text(encoding="utf-8") == "existing user summary"
    assert (job_path.parent / "transcript.md").read_bytes() == before
    assert not list(job_path.parent.glob("*.superseded-*.md"))


def test_partial_archive_failure_restores_all_summaries(
    workspace_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_path, summary = completed_job_with_summary(workspace_tmp_path, monkeypatch)
    target = job_path.parent / "BVTEST_summary_en.md"
    target.write_text("existing target summary", encoding="utf-8")
    original_rename = Path.rename
    calls = 0

    def fail_second_rename(source: Path, destination: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("rename denied")
        return original_rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_second_rename)
    with pytest.raises(PermissionError, match="rename denied"):
        continue_summary.continue_summary(job_path, summary_language="en")

    assert read_json(job_path)["status"] == "prompt_ready"
    assert summary.read_text(encoding="utf-8") == "existing user summary"
    assert target.read_text(encoding="utf-8") == "existing target summary"
    assert not list(job_path.parent.glob("*.superseded-*.md"))


def test_continue_rejects_video_id_that_could_escape_output_directory(
    workspace_tmp_path: Path,
) -> None:
    job_path, _ = make_needs_job(workspace_tmp_path)
    job = read_json(job_path)
    job["video"]["bvid"] = "../outside"
    write_json(job_path, job)

    with pytest.raises(ValueError, match="video.bvid"):
        continue_summary.continue_summary(job_path)

    assert read_json(job_path) == job
