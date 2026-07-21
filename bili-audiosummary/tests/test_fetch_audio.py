import argparse
from pathlib import Path

import pytest

import scripts.fetch_audio as fetch_audio
from scripts.process_logging import LoggingSession
from scripts.utils import read_json


def make_args(workspace_tmp_path: Path, skip_subtitles: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        url="https://www.bilibili.com/video/BVTEST/",
        output_dir=workspace_tmp_path,
        cookies=None,
        playlist=False,
        skip_audio=False,
        skip_subtitles=skip_subtitles,
        language="zh",
        write_auto_subs=True,
        subtitle_langs=[],
        subtitle_format="srt/best",
        audio_selector=fetch_audio.DEFAULT_AUDIO_SELECTOR,
        audio_format=fetch_audio.DEFAULT_AUDIO_CODEC,
        audio_quality="0",
        retries=10,
        socket_timeout=30,
        quiet=True,
    )


def test_build_canonical_url_preserves_non_first_page() -> None:
    info = {
        "id": "BVTEST_p2",
        "webpage_url": "https://www.bilibili.com/video/BVTEST/?spm_id=x&p=2",
    }

    assert fetch_audio.build_canonical_url(info, "BVTEST_p2") == "https://www.bilibili.com/video/BVTEST/?p=2"


def test_build_canonical_url_preserves_first_page() -> None:
    info = {
        "id": "BVTEST_p1",
        "webpage_url": "https://www.bilibili.com/video/BVTEST/?p=1",
    }

    assert fetch_audio.build_canonical_url(info, "BVTEST_p1") == "https://www.bilibili.com/video/BVTEST/?p=1"


def test_select_valid_srt_files_filters_invalid_subtitles(
    sample_srt_path: Path,
    invalid_srt_path: Path,
) -> None:
    valid_files = fetch_audio.select_valid_srt_files(
        [invalid_srt_path, sample_srt_path],
        ["zh-Hans"],
        "Cached",
    )

    assert valid_files == [sample_srt_path]


def test_make_base_options_requires_packaged_ffmpeg(mocker) -> None:
    mocker.patch("scripts.fetch_audio.resolve_ffmpeg_location", return_value=None)

    with pytest.raises(RuntimeError, match=r"scripts\\setup\\setup_windows\.bat"):
        fetch_audio.make_base_options(make_args(Path(".")))


def test_run_fetch_skip_subtitles_does_not_download_subtitles(workspace_tmp_path: Path, mocker) -> None:
    info = {
        "id": "BVTEST",
        "title": "测试视频",
        "webpage_url": "https://www.bilibili.com/video/BVTEST/",
    }
    audio_path = workspace_tmp_path / "BVTEST" / "resource" / "BVTEST.m4a"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")

    mocker.patch("scripts.fetch_audio.extract_metadata", return_value=info)
    download_subtitles = mocker.patch("scripts.fetch_audio.download_subtitles")
    mocker.patch("scripts.fetch_audio.download_audio", return_value=[audio_path])

    result = fetch_audio.run_fetch(make_args(workspace_tmp_path, skip_subtitles=True))
    manifest = read_json(result["manifest_path"])

    download_subtitles.assert_not_called()
    assert result["subtitle_files"] == []
    assert manifest["subtitle_files"] == []
    assert manifest["audio_files"] == [audio_path.as_posix()]


def test_run_fetch_uses_canonical_url_for_watchlater_video(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    watchlater_url = (
        "https://www.bilibili.com/list/watchlater/"
        "?bvid=BV1W1JxzjEty&oid=114827194271844"
    )
    canonical_url = "https://www.bilibili.com/video/BV1W1JxzjEty/"
    info = {
        "id": "BV1W1JxzjEty",
        "title": "测试视频",
        "webpage_url": canonical_url,
    }
    args = make_args(workspace_tmp_path)
    args.url = watchlater_url

    extract_metadata = mocker.patch("scripts.fetch_audio.extract_metadata", return_value=info)
    download_subtitles = mocker.patch(
        "scripts.fetch_audio.download_subtitles",
        return_value=[],
    )
    download_audio = mocker.patch("scripts.fetch_audio.download_audio", return_value=[])

    result = fetch_audio.run_fetch(args)
    manifest = read_json(result["manifest_path"])

    assert extract_metadata.call_args.args[0] == canonical_url
    assert download_subtitles.call_args.args[0] == canonical_url
    assert download_audio.call_args.args[0] == canonical_url
    assert manifest["url"] == canonical_url


def test_run_fetch_downloads_only_selected_page(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    selected_page_url = "https://www.bilibili.com/video/BVTEST/?p=2"
    info = {
        "id": "BVTEST_p2",
        "title": "测试视频 p02 第二集",
        "webpage_url": selected_page_url,
    }
    args = make_args(workspace_tmp_path)
    args.url = f"{selected_page_url}&spm_id_from=333.788"

    extract_metadata = mocker.patch(
        "scripts.fetch_audio.extract_metadata", return_value=info
    )
    mocker.patch("scripts.fetch_audio.download_subtitles", return_value=[])
    download_audio = mocker.patch(
        "scripts.fetch_audio.download_audio", return_value=[]
    )

    result = fetch_audio.run_fetch(args)
    manifest = read_json(result["manifest_path"])

    assert extract_metadata.call_args.args[0] == selected_page_url
    assert download_audio.call_args.args[0] == selected_page_url
    assert result["video_id"] == "BVTEST_p2"
    assert result["paths"]["result"] == workspace_tmp_path / "BVTEST_p2"
    assert manifest["url"] == selected_page_url


def test_run_fetch_only_emits_stage_extraction_and_title(
    workspace_tmp_path: Path,
    capsys,
    mocker,
) -> None:
    info = {
        "id": "BVTEST",
        "title": "测试视频",
        "webpage_url": "https://www.bilibili.com/video/BVTEST/",
    }
    audio_path = workspace_tmp_path / "BVTEST" / "resource" / "BVTEST.m4a"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")
    mocker.patch("scripts.fetch_audio.extract_metadata", return_value=info)
    mocker.patch("scripts.fetch_audio.download_audio", return_value=[audio_path])

    with LoggingSession(workspace_tmp_path / "fetch.log"):
        fetch_audio.run_fetch(make_args(workspace_tmp_path, skip_subtitles=True))

    assert capsys.readouterr().out == (
        "[Stage] Fetch metadata, subtitles, and audio\n"
        "[BiliBili] Extracting URL: https://www.bilibili.com/video/BVTEST/\n"
        "Title: 测试视频\n"
    )
