import argparse
from pathlib import Path

import pytest

import scripts.complete_summary as complete_summary
import scripts.continue_summary as continue_summary
import scripts.prepare_audio as prepare_audio
import scripts.run_pipeline as run_pipeline
from scripts.runtime_paths import RuntimePaths
from scripts.transcription_input import TranscriptionInput, TranscriptionInputError
from scripts.utils import read_json, write_json, write_json_atomic


@pytest.fixture(autouse=True)
def use_test_results_dir(workspace_tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BILI_AUDIOSUMMARY_DATA_DIR", str(workspace_tmp_path))


def make_args(
    skip_subtitles: bool = False,
    url: str = "https://www.bilibili.com/video/BVTEST/",
) -> argparse.Namespace:
    return argparse.Namespace(
        url=url,
        cookies=None,
        language="zh",
        summary_language=None,
        skip_subtitles=skip_subtitles,
    )


def make_fetch_result(
    workspace_tmp_path: Path,
    *,
    subtitle_text: str | None = None,
    audio: bool = True,
    video_id: str = "BVTEST",
    url: str = "https://www.bilibili.com/video/BVTEST/",
) -> dict:
    result_dir = workspace_tmp_path / "results" / video_id
    resource_dir = result_dir / "resource"
    subtitle_dir = resource_dir / "subtitle"
    subtitle_dir.mkdir(parents=True)
    audio_files = []
    if audio:
        audio_path = resource_dir / f"{video_id}.m4a"
        audio_path.write_bytes(b"audio")
        audio_files.append(audio_path)
    subtitle_files = []
    if subtitle_text is not None:
        subtitle_path = subtitle_dir / f"{video_id}.zh-Hans.srt"
        subtitle_path.write_text(subtitle_text, encoding="utf-8")
        subtitle_files.append(subtitle_path)

    metadata_path = resource_dir / "metadata.json"
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(metadata_path, {"uploader": "测试作者"})
    write_json(
        manifest_path,
        {
            "id": video_id,
            "title": "测试视频",
            "url": url,
            "metadata_path": metadata_path.as_posix(),
            "audio_files": [path.as_posix() for path in audio_files],
            "subtitle_files": [path.as_posix() for path in subtitle_files],
        },
    )
    return {
        "video_id": video_id,
        "paths": {"result": result_dir},
        "metadata_path": metadata_path,
        "manifest_path": manifest_path,
        "audio_files": audio_files,
        "subtitle_files": subtitle_files,
    }


def test_prepare_with_subtitle_writes_prompt_ready_job_atomically(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    fetch_result = make_fetch_result(
        workspace_tmp_path,
        subtitle_text=("1\n00:00:00,000 --> 00:00:01,000\n第一句话。\n"),
    )
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch_result
    )

    result = run_pipeline.run_pipeline(make_args())

    job = read_json(result["job_path"])
    assert job == result["job"]
    assert set(job) == {
        "status",
        "video",
        "resources",
        "transcript",
        "prompt",
        "error",
    }
    assert job["status"] == "prompt_ready"
    assert job["transcript"]["source"] == "bilibili_subtitle"
    assert job["resources"]["subtitle"] == "resource/subtitle/BVTEST.zh-Hans.srt"
    assert (result["job_path"].parent / "transcript.md").is_file()
    assert (result["job_path"].parent / job["prompt"]["path"]).is_file()
    prompt = (result["job_path"].parent / job["prompt"]["path"]).read_text(
        encoding="utf-8"
    )
    assert "[读取转写数据](transcript.md)" in prompt
    assert "summary_job.json" not in prompt
    assert "fetch_manifest.json" not in prompt
    assert "_transcript.json" not in prompt
    assert not result["job_path"].with_suffix(".json.tmp").exists()


@pytest.mark.parametrize(
    ("subtitle_text", "skip_subtitles", "expected_status"),
    [
        ("1\n00:00:00,000 --> 00:00:01,000\n字幕。\n", False, "prompt_ready"),
        (None, False, "needs_transcription"),
        ("", False, "needs_transcription"),
        ("not srt", False, "needs_transcription"),
        ("1\n00:00:00,000 --> 00:00:00,000\n字幕。\n", False, "needs_transcription"),
        ("1\n00:00:02,000 --> 00:00:01,000\n字幕。\n", False, "needs_transcription"),
        ("1\n00:00:00,000 --> 00:00:01,000\n字幕。\n", True, "needs_transcription"),
    ],
)
def test_pipeline_fetches_audio_only_when_subtitles_are_unusable(
    workspace_tmp_path, mocker, subtitle_text, skip_subtitles, expected_status
):
    initial = make_fetch_result(
        workspace_tmp_path, subtitle_text=subtitle_text, audio=False
    )
    mocker.patch(
        "scripts.fetch_audio.extract_metadata",
        return_value={
            "id": "BVTEST",
            "title": "测试视频",
            "webpage_url": "https://www.bilibili.com/video/BVTEST/",
        },
    )
    subtitles = mocker.patch(
        "scripts.fetch_audio.download_subtitles", return_value=initial["subtitle_files"]
    )

    def download(_url, directory, video_id, _options):
        audio_path = directory / f"{video_id}.m4a"
        audio_path.write_bytes(b"audio")
        return [audio_path]

    audio = mocker.patch("scripts.fetch_audio.download_audio", side_effect=download)
    prepared = run_pipeline.run_pipeline(make_args(skip_subtitles=skip_subtitles))
    job = prepared["job"]
    assert job["status"] == expected_status
    if expected_status == "prompt_ready":
        audio.assert_not_called()
        assert job["resources"]["audio"] is None
        summary = prepared["job_path"].parent / job["prompt"]["summary_path"]
        summary.write_text("字幕内容的中文总结。", encoding="utf-8")
        completed, validation = complete_summary.complete_summary(prepared["job_path"])
        assert validation.ok
        assert completed["status"] == "complete"
        restored = run_pipeline.run_pipeline(make_args())
        assert restored["job"] == completed
        audio.assert_not_called()
    else:
        audio.assert_called_once()
        assert job["resources"]["audio"] == "resource/BVTEST.m4a"
        manifest = read_json(initial["manifest_path"])
        assert manifest["audio_files"] == [
            (prepared["job_path"].parent / job["resources"]["audio"]).as_posix()
        ]
    if skip_subtitles:
        subtitles.assert_not_called()


def make_completed_subtitle_job(root, mocker, *, video_id="BVTEST"):
    url = "https://www.bilibili.com/video/BVTEST/"
    if "_p" in video_id:
        url += f"?p={video_id.split('_p')[1]}"
    fetched = make_fetch_result(
        root,
        subtitle_text="1\n00:00:00,000 --> 00:00:01,000\n字幕。\n",
        audio=False,
        video_id=video_id,
        url=url,
    )
    mocker.patch("scripts.fetch_audio.run_fetch", return_value=fetched)
    prepared = run_pipeline.run_pipeline(make_args(url=url))
    job_path = prepared["job_path"]
    summary_path = job_path.parent / prepared["job"]["prompt"]["summary_path"]
    summary_path.write_text("保留现有的中文总结。", encoding="utf-8")
    complete_summary.complete_summary(job_path)
    return job_path, summary_path


@pytest.mark.parametrize("video_id", ["BVTEST", "BVTEST_p2"])
def test_prepare_audio_preserves_completed_input_until_transcription_import(
    workspace_tmp_path, mocker, video_id
):
    job_path, summary_path = make_completed_subtitle_job(
        workspace_tmp_path, mocker, video_id=video_id
    )
    before = read_json(job_path)
    snapshots = {
        path: path.read_bytes()
        for path in job_path.parent.iterdir()
        if path.suffix in {".json", ".md"} and path != job_path
    }
    cookies = workspace_tmp_path / "cookies.txt"

    def download(url, directory, requested_video_id, options):
        assert requested_video_id == video_id
        assert url == before["video"]["url"]
        assert options.cookies == cookies
        audio_path = directory / f"{video_id}.m4a"
        audio_path.write_bytes(b"audio")
        return [audio_path]

    audio = mocker.patch("scripts.fetch_audio.download_audio", side_effect=download)
    job = prepare_audio.prepare_audio(job_path, cookies=cookies)
    assert job == {
        **before,
        "resources": {**before["resources"], "audio": f"resource/{video_id}.m4a"},
    }
    assert all(path.read_bytes() == data for path, data in snapshots.items())
    assert prepare_audio.prepare_audio(job_path) == job
    audio.assert_called_once()

    audio_path = job_path.parent / job["resources"]["audio"]
    mocker.patch(
        "scripts.continue_summary.load_transcription",
        return_value=TranscriptionInput(
            audio_id=continue_summary._file_sha256(audio_path),
            language="zh",
            duration=1.0,
            segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "新转写。"}],
        ),
    )
    updated = continue_summary.continue_summary(
        job_path, (workspace_tmp_path / "manifest.json").resolve()
    )
    assert updated["status"] == "prompt_ready"
    assert updated["transcript"]["source"] == "audio_transcribe"
    assert not summary_path.exists()
    archived = list(job_path.parent.glob("*.superseded-*.md"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "保留现有的中文总结。"


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("network unavailable"),
        PermissionError("denied"),
        run_pipeline.fetch_audio.CookieRequiredError("HTTP 412"),
    ],
)
def test_prepare_audio_failure_preserves_completed_job(
    workspace_tmp_path, mocker, failure
):
    job_path, summary_path = make_completed_subtitle_job(workspace_tmp_path, mocker)
    before = job_path.read_bytes()
    manifest_path = job_path.parent / "resource/fetch_manifest.json"
    manifest_before = manifest_path.read_bytes()
    mocker.patch("scripts.fetch_audio.download_audio", side_effect=failure)
    with pytest.raises(type(failure)):
        prepare_audio.prepare_audio(job_path)
    assert job_path.read_bytes() == before
    assert manifest_path.read_bytes() == manifest_before
    assert summary_path.read_text(encoding="utf-8") == "保留现有的中文总结。"


def test_import_without_prepared_audio_explains_next_step(workspace_tmp_path, mocker):
    job_path, _ = make_completed_subtitle_job(workspace_tmp_path, mocker)
    loader = mocker.patch("scripts.continue_summary.load_transcription")
    with pytest.raises(TranscriptionInputError, match="scripts.prepare_audio"):
        continue_summary.continue_summary(job_path, workspace_tmp_path.resolve())
    loader.assert_not_called()


@pytest.mark.parametrize("boundary", ["manifest", "job"])
def test_prepare_audio_publish_failure_can_retry_without_replacing_summary(
    workspace_tmp_path, mocker, boundary
):
    job_path, summary_path = make_completed_subtitle_job(workspace_tmp_path, mocker)
    before = job_path.read_bytes()
    audio_path = job_path.parent / "resource/BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    downloader = mocker.patch(
        "scripts.fetch_audio.download_audio", return_value=[audio_path]
    )
    operation = "write_json_atomic" if boundary == "manifest" else "publish_job"
    failure = mocker.patch(
        f"scripts.prepare_audio.{operation}", side_effect=PermissionError("denied")
    )
    with pytest.raises(PermissionError):
        prepare_audio.prepare_audio(job_path)
    assert job_path.read_bytes() == before
    assert summary_path.read_text(encoding="utf-8") == "保留现有的中文总结。"
    mocker.stop(failure)
    mocker.stop(downloader)
    # 重试复用已下载音频，无需再次访问网络。
    network = mocker.patch(
        "scripts.fetch_audio.make_base_options", side_effect=AssertionError
    )
    restored = prepare_audio.prepare_audio(job_path)
    assert restored["status"] == "complete"
    assert restored["resources"]["audio"] == "resource/BVTEST.m4a"
    assert summary_path.is_file()
    network.assert_not_called()


def test_prepare_audio_main_reports_cookie_failure(workspace_tmp_path, mocker):
    job_path, _ = make_completed_subtitle_job(workspace_tmp_path, mocker)
    mocker.patch(
        "scripts.fetch_audio.download_audio",
        side_effect=run_pipeline.fetch_audio.CookieRequiredError("HTTP 412"),
    )
    assert prepare_audio.main([str(job_path)]) == 2


def test_prepare_audio_rejects_job_outside_configured_results(workspace_tmp_path):
    with pytest.raises(ValueError):
        prepare_audio.prepare_audio(
            workspace_tmp_path / "outside/summary_job.json",
            paths=RuntimePaths.resolve(workspace_tmp_path / "data"),
        )


def test_prepare_audio_does_not_replace_missing_registered_audio(
    workspace_tmp_path, mocker
):
    job_path, summary_path = make_completed_subtitle_job(workspace_tmp_path, mocker)
    job = read_json(job_path)
    job["resources"]["audio"] = "resource/missing.m4a"
    write_json_atomic(job_path, job)
    downloader = mocker.patch("scripts.fetch_audio.download_audio")
    with pytest.raises(ValueError, match="Registered audio does not exist"):
        prepare_audio.prepare_audio(job_path)
    downloader.assert_not_called()
    assert read_json(job_path) == job
    assert summary_path.is_file()


@pytest.mark.parametrize("status", ["preparing", "failed"])
def test_prepare_audio_rejects_unstable_job(workspace_tmp_path, mocker, status):
    fetched = make_fetch_result(workspace_tmp_path, audio=False)
    job_path, job = run_pipeline._preparing_job(make_args())
    if status == "failed":
        job = run_pipeline._failed_job(job, "fetch", RuntimeError("failure"))
    write_json_atomic(job_path, job)
    downloader = mocker.patch("scripts.fetch_audio.download_audio")
    with pytest.raises(ValueError, match="cannot prepare audio"):
        prepare_audio.prepare_audio(job_path)
    downloader.assert_not_called()
    assert read_json(job_path) == job
    assert fetched["manifest_path"].is_file()


@pytest.mark.parametrize("page", ["0", "-1", "../secret"])
def test_prepare_rejects_invalid_video_page(
    workspace_tmp_path: Path, page: str, monkeypatch
) -> None:
    monkeypatch.setattr(
        run_pipeline,
        "normalize_bilibili_video_url",
        lambda _url: f"https://www.bilibili.com/video/BVTEST/?p={page}",
    )

    with pytest.raises(ValueError, match="page must be a positive integer"):
        run_pipeline._preparing_job(make_args())


def test_prepare_rejects_result_path_escape(monkeypatch) -> None:
    monkeypatch.setattr(
        run_pipeline, "normalize_bilibili_video_url", lambda _url: "normalized"
    )
    monkeypatch.setattr(
        run_pipeline, "_video_id_from_normalized_url", lambda _url: "../escape"
    )

    with pytest.raises(ValueError, match="escapes the results directory"):
        run_pipeline._preparing_job(make_args())


def test_prepare_with_mixed_zero_duration_subtitle_stays_prompt_ready(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    fetch_result = make_fetch_result(
        workspace_tmp_path,
        subtitle_text=(
            "1\n00:00:00,000 --> 00:00:00,000\nskip me\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nkeep me\n"
        ),
    )
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch_result
    )

    result = run_pipeline.run_pipeline(make_args())
    transcript = read_json(
        result["job_path"].parent / result["job"]["transcript"]["path"]
    )

    assert result["job"]["status"] == "prompt_ready"
    assert transcript["segments"] == [
        {"id": 0, "start": 1.0, "end": 2.0, "text": "keep me"}
    ]
    markdown = (result["job_path"].parent / "transcript.md").read_text(encoding="utf-8")
    assert "keep me" in markdown
    assert "skip me" not in markdown


def test_prepare_with_only_zero_duration_subtitle_needs_transcription(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    fetch_result = make_fetch_result(
        workspace_tmp_path,
        subtitle_text="1\n00:00:00,000 --> 00:00:00,000\nskip me\n",
    )
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch_result
    )

    result = run_pipeline.run_pipeline(make_args())

    assert result["job"]["status"] == "needs_transcription"
    assert result["job"]["resources"]["subtitle"] is None
    assert result["job"]["transcript"] is None


@pytest.mark.parametrize("skip_subtitles", [False, True])
def test_prepare_without_selected_subtitle_stops_for_external_transcription(
    workspace_tmp_path: Path,
    mocker,
    skip_subtitles: bool,
) -> None:
    subtitle = "1\n00:00:00,000 --> 00:00:01,000\n字幕。\n" if skip_subtitles else None
    fetch_result = make_fetch_result(workspace_tmp_path, subtitle_text=subtitle)
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch_result
    )

    result = run_pipeline.run_pipeline(make_args(skip_subtitles=skip_subtitles))

    job = result["job"]
    assert job["status"] == "needs_transcription"
    assert job["resources"]["audio"] == "resource/BVTEST.m4a"
    assert job["resources"]["subtitle_skipped"] is skip_subtitles
    assert job["transcript"] is None
    assert job["prompt"] is None


def test_prepare_failure_after_preparing_writes_failed_job(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    fetch_result = make_fetch_result(workspace_tmp_path, audio=False)
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch_result
    )
    mocker.patch("scripts.fetch_audio.download_audio", return_value=[])

    with pytest.raises(RuntimeError, match="No usable audio"):
        run_pipeline.run_pipeline(make_args())

    job_path = fetch_result["paths"]["result"] / "summary_job.json"
    job = read_json(job_path)
    assert job["status"] == "failed"
    assert job["error"] == {
        "stage": "prepare_audio",
        "type": "RuntimeError",
        "message": "No usable audio file is available for external transcription.",
    }
    assert "traceback" not in job


def test_prepare_publishes_preparing_before_fetch(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    fetch_result = make_fetch_result(workspace_tmp_path)
    job_path = fetch_result["paths"]["result"] / "summary_job.json"

    def fake_fetch(_options):
        assert read_json(job_path)["status"] == "preparing"
        return fetch_result

    mocker.patch("scripts.run_pipeline.fetch_audio.run_fetch", side_effect=fake_fetch)

    assert run_pipeline.run_pipeline(make_args())["job"]["status"] == (
        "needs_transcription"
    )


def test_prepare_publishes_page_preparing_before_fetch(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    page_url = "https://www.bilibili.com/video/BVTEST/?p=2"
    fetch_result = make_fetch_result(
        workspace_tmp_path,
        video_id="BVTEST_p2",
        url=page_url,
    )
    job_path = fetch_result["paths"]["result"] / "summary_job.json"

    def fake_fetch(_options):
        assert read_json(job_path)["status"] == "preparing"
        return fetch_result

    mocker.patch("scripts.run_pipeline.fetch_audio.run_fetch", side_effect=fake_fetch)

    result = run_pipeline.run_pipeline(make_args(url=page_url))

    assert result["job_path"] == job_path
    assert result["job"]["video"]["bvid"] == "BVTEST_p2"


def test_fetch_failure_does_not_persist_sensitive_exception_text(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch",
        side_effect=RuntimeError("cookie=secret transcript=private"),
    )

    with pytest.raises(RuntimeError, match="cookie=secret"):
        run_pipeline.run_pipeline(make_args())

    job = read_json(workspace_tmp_path / "results" / "BVTEST" / "summary_job.json")
    assert job["status"] == "failed"
    assert job["error"]["message"] == "Bilibili resource preparation failed."
    assert "secret" not in str(job)
    assert "private" not in str(job)


@pytest.mark.parametrize("status", ["prompt_ready", "complete"])
def test_prepare_restores_existing_ready_job_without_refetch(
    workspace_tmp_path: Path,
    mocker,
    status: str,
) -> None:
    fetch_result = make_fetch_result(workspace_tmp_path)
    job_path = fetch_result["paths"]["result"] / "summary_job.json"
    transcript_path = job_path.parent / "BVTEST_transcript.json"
    prompt_path = job_path.parent / "BVTEST_summary_prompt.md"
    write_json(
        transcript_path,
        {
            "language": "zh-Hans",
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "keep"}],
        },
    )
    prompt_path.write_text("keep", encoding="utf-8")
    existing = {
        "status": status,
        "video": {
            "bvid": "BVTEST",
            "title": "测试视频",
            "url": "https://www.bilibili.com/video/BVTEST/",
        },
        "resources": {
            "fetch_manifest": "resource/fetch_manifest.json",
            "subtitle": "resource/subtitle/BVTEST.zh-Hans.srt",
            "audio": "resource/BVTEST.m4a",
            "subtitle_skipped": False,
        },
        "transcript": {
            "source": "bilibili_subtitle",
            "path": "BVTEST_transcript.json",
        },
        "prompt": {
            "path": "BVTEST_summary_prompt.md",
            "summary_path": "BVTEST_summary_zh.md",
        },
        "error": None,
    }
    write_json_atomic(job_path, existing)
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch_result
    )

    result = run_pipeline.run_pipeline(make_args())

    assert result["job"]["status"] == status
    assert "keep" in (job_path.parent / "transcript.md").read_text(encoding="utf-8")
    run_pipeline.fetch_audio.run_fetch.assert_not_called()


@pytest.mark.parametrize("status", ["prompt_ready", "complete"])
def test_prepare_page_job_does_not_touch_existing_base_ready_job(
    workspace_tmp_path: Path,
    mocker,
    status: str,
) -> None:
    page_url = "https://www.bilibili.com/video/BVTEST/?p=2"
    fetch_result = make_fetch_result(
        workspace_tmp_path,
        video_id="BVTEST_p2",
        url=page_url,
    )
    base_job_path = workspace_tmp_path / "results" / "BVTEST" / "summary_job.json"
    existing = {
        "status": status,
        "video": {
            "bvid": "BVTEST",
            "title": "测试视频",
            "url": "https://www.bilibili.com/video/BVTEST/",
        },
        "resources": {
            "fetch_manifest": "resource/fetch_manifest.json",
            "subtitle": "resource/subtitle/BVTEST.zh-Hans.srt",
            "audio": "resource/BVTEST.m4a",
            "subtitle_skipped": False,
        },
        "transcript": {
            "source": "bilibili_subtitle",
            "path": "BVTEST_transcript.json",
        },
        "prompt": {
            "path": "BVTEST_summary_prompt.md",
            "summary_path": "BVTEST_summary_zh.md",
        },
        "error": None,
    }
    write_json_atomic(base_job_path, existing)
    mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch_result
    )

    result = run_pipeline.run_pipeline(make_args(url=page_url))

    assert result["job_path"] == fetch_result["paths"]["result"] / "summary_job.json"
    assert read_json(base_job_path) == existing


def test_parse_args_has_no_asr_model_options(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_pipeline.py",
            "https://www.bilibili.com/video/BVTEST/",
            "--language",
            "zh",
        ],
    )
    args = run_pipeline.parse_args()
    assert not hasattr(args, "asr_provider")
    assert not hasattr(args, "model")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(12.34, "12.34s"), (72.34, "1m 12.34s"), (3723.45, "1h 2m 03.45s")],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert run_pipeline.format_duration(seconds) == expected


@pytest.mark.parametrize("conflict", ["language", "skip_subtitles"])
def test_existing_subtitle_job_rejects_conflicting_input_selection(
    workspace_tmp_path: Path,
    mocker,
    conflict: str,
) -> None:
    fetch = make_fetch_result(
        workspace_tmp_path,
        subtitle_text="1\n00:00:00,000 --> 00:00:01,000\n现有字幕。\n",
    )
    downloader = mocker.patch(
        "scripts.run_pipeline.fetch_audio.run_fetch", return_value=fetch
    )
    first = run_pipeline.run_pipeline(make_args())
    before = read_json(first["job_path"])
    before_transcript = (first["job_path"].parent / "transcript.md").read_bytes()
    downloader.reset_mock()
    arguments = make_args()
    if conflict == "language":
        arguments.language = "en"
    else:
        arguments.skip_subtitles = True

    with pytest.raises(ValueError, match="Existing.*--transcription-manifest"):
        run_pipeline.run_pipeline(arguments)

    downloader.assert_not_called()
    assert read_json(first["job_path"]) == before
    assert (
        first["job_path"].parent / "transcript.md"
    ).read_bytes() == before_transcript
