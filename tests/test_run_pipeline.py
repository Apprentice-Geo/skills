import argparse
from pathlib import Path

import pytest

import run_pipeline
from utils import write_json


def make_args(skip_subtitles: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        url="https://www.bilibili.com/video/BVTEST/",
        cookies=None,
        language="zh",
        asr_provider="whisper",
        skip_subtitles=skip_subtitles,
    )


def make_fetch_result(
    workspace_tmp_path: Path,
    subtitle_files: list[Path],
    audio_files: list[Path],
    manifest_payload: dict,
    metadata_payload: dict,
) -> dict:
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    resource_dir.mkdir(parents=True)

    metadata_path = resource_dir / "metadata.json"
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(metadata_path, metadata_payload)
    write_json(
        manifest_path,
        {
            **manifest_payload,
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


def make_transcribe_result(result_dir: Path) -> dict:
    json_path = result_dir / "BVTEST_transcript.json"
    markdown_path = result_dir / "BVTEST_transcript.md"
    write_json(
        json_path,
        {
            "bvid": "BVTEST",
            "title": "测试视频",
            "url": "https://www.bilibili.com/video/BVTEST/",
            "source": "faster-whisper",
            "language": "zh",
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "ASR 文本"}],
        },
    )
    markdown_path.write_text(
        "## metadata\n\nsource: faster-whisper\nlanguage: zh\n\n## transcript text\n\n[00:00:00 - 00:00:01] ASR 文本\n",
        encoding="utf-8",
    )
    return {"json_path": json_path, "markdown_path": markdown_path, "segments": []}


def test_pipeline_prefers_usable_subtitle_without_calling_asr(
    workspace_tmp_path: Path,
    sample_srt_path: Path,
    manifest_payload: dict,
    metadata_payload: dict,
    mocker,
) -> None:
    audio_path = workspace_tmp_path / "BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    fetch_result = make_fetch_result(
        workspace_tmp_path,
        [sample_srt_path],
        [audio_path],
        manifest_payload,
        metadata_payload,
    )
    mocker.patch("run_pipeline.fetch_audio.run_fetch", return_value=fetch_result)
    transcribe_mock = mocker.patch("run_pipeline.transcribe.run_transcribe")

    result = run_pipeline.run_pipeline(make_args())

    transcribe_mock.assert_not_called()
    assert result["transcript"]["payload"]["source"] == "subtitle"
    assert result["prompt"]["prompt_path"].exists()


def test_skip_subtitles_forces_asr_even_when_subtitle_exists(
    workspace_tmp_path: Path,
    sample_srt_path: Path,
    manifest_payload: dict,
    metadata_payload: dict,
    mocker,
) -> None:
    audio_path = workspace_tmp_path / "BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    fetch_result = make_fetch_result(
        workspace_tmp_path,
        [sample_srt_path],
        [audio_path],
        manifest_payload,
        metadata_payload,
    )
    run_fetch_mock = mocker.patch("run_pipeline.fetch_audio.run_fetch", return_value=fetch_result)
    transcribe_mock = mocker.patch(
        "run_pipeline.transcribe.run_transcribe",
        return_value=make_transcribe_result(fetch_result["paths"]["result"]),
    )

    result = run_pipeline.run_pipeline(make_args(skip_subtitles=True))

    fetch_args = run_fetch_mock.call_args.args[0]
    assert fetch_args.skip_subtitles is True
    transcribe_mock.assert_called_once()
    assert result["transcript"]["json_path"].exists()
    assert result["prompt"]["summary_path"].name == "BVTEST_summary_zh.md"


def test_pipeline_fails_before_asr_when_no_audio_is_available(
    workspace_tmp_path: Path,
    manifest_payload: dict,
    metadata_payload: dict,
    mocker,
) -> None:
    fetch_result = make_fetch_result(workspace_tmp_path, [], [], manifest_payload, metadata_payload)
    mocker.patch("run_pipeline.fetch_audio.run_fetch", return_value=fetch_result)
    transcribe_mock = mocker.patch("run_pipeline.transcribe.run_transcribe")

    with pytest.raises(RuntimeError, match="No usable audio files available"):
        run_pipeline.run_pipeline(make_args(skip_subtitles=True))

    transcribe_mock.assert_not_called()
