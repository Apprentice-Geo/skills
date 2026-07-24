import argparse
from pathlib import Path

import pytest

import scripts.run_pipeline as run_pipeline
from scripts.utils import read_json, write_json, write_json_atomic


@pytest.fixture(autouse=True)
def use_test_results_dir(workspace_tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run_pipeline, "RESULTS_DIR", workspace_tmp_path / "results")


def make_args(skip_subtitles: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        url="https://www.bilibili.com/video/BVTEST/",
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
) -> dict:
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    subtitle_dir = resource_dir / "subtitle"
    subtitle_dir.mkdir(parents=True)
    audio_files = []
    if audio:
        audio_path = resource_dir / "BVTEST.m4a"
        audio_path.write_bytes(b"audio")
        audio_files.append(audio_path)
    subtitle_files = []
    if subtitle_text is not None:
        subtitle_path = subtitle_dir / "BVTEST.zh-Hans.srt"
        subtitle_path.write_text(subtitle_text, encoding="utf-8")
        subtitle_files.append(subtitle_path)

    metadata_path = resource_dir / "metadata.json"
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(metadata_path, {"uploader": "测试作者"})
    write_json(
        manifest_path,
        {
            "id": "BVTEST",
            "title": "测试视频",
            "url": "https://www.bilibili.com/video/BVTEST/",
            "metadata_path": metadata_path.as_posix(),
            "audio_files": [path.as_posix() for path in audio_files],
            "subtitle_files": [path.as_posix() for path in subtitle_files],
        },
    )
    return {
        "video_id": "BVTEST",
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
        "schema_version",
        "status",
        "video",
        "resources",
        "transcript",
        "transcription_manifest",
        "prompt",
        "error",
    }
    assert job["status"] == "prompt_ready"
    assert job["transcript"]["source"] == "bilibili_subtitle"
    assert job["resources"]["subtitle"] == "resource/subtitle/BVTEST.zh-Hans.srt"
    assert job["transcription_manifest"] is None
    assert (result["job_path"].parent / job["prompt"]["path"]).is_file()
    assert not result["job_path"].with_suffix(".json.tmp").exists()


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

    with pytest.raises(RuntimeError, match="No usable audio"):
        run_pipeline.run_pipeline(make_args())

    job_path = fetch_result["paths"]["result"] / "summary_job.json"
    job = read_json(job_path)
    assert job["status"] == "failed"
    assert job["error"] == {
        "stage": "prepare_transcript",
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
def test_prepare_does_not_overwrite_existing_ready_job(
    workspace_tmp_path: Path,
    mocker,
    status: str,
) -> None:
    fetch_result = make_fetch_result(workspace_tmp_path)
    job_path = fetch_result["paths"]["result"] / "summary_job.json"
    transcript_path = job_path.parent / "BVTEST_transcript.json"
    prompt_path = job_path.parent / "BVTEST_summary_prompt.md"
    write_json(transcript_path, {"segments": [{"text": "keep"}]})
    prompt_path.write_text("keep", encoding="utf-8")
    existing = {
        "schema_version": 1,
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
        "transcription_manifest": None,
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

    with pytest.raises(RuntimeError, match=f"existing {status} job"):
        run_pipeline.run_pipeline(make_args())

    assert read_json(job_path) == existing
    assert prompt_path.read_text(encoding="utf-8") == "keep"


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
