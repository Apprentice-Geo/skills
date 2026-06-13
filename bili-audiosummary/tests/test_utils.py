import sys
import types
from pathlib import Path

import utils


def test_normalize_bilibili_watchlater_url_returns_canonical_video_url() -> None:
    url = (
        "https://www.bilibili.com/list/watchlater/"
        "?bvid=BV1W1JxzjEty&oid=114827194271844"
    )

    assert utils.normalize_bilibili_video_url(url) == (
        "https://www.bilibili.com/video/BV1W1JxzjEty/"
    )


def make_ffmpeg_module(ffmpeg_path: Path) -> types.ModuleType:
    module = types.ModuleType("ffmpeg_binaries")
    module.FFMPEG_PATH = str(ffmpeg_path)
    module.init = lambda: None
    return module


def test_resolve_ffmpeg_binaries_location_returns_packaged_binary_directory(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    bin_dir = workspace_tmp_path / "ffmpeg"
    bin_dir.mkdir()
    ffmpeg = bin_dir / "ffmpeg.exe"
    ffprobe = bin_dir / "ffprobe.exe"
    ffmpeg.write_bytes(b"")
    ffprobe.write_bytes(b"")
    monkeypatch.setitem(sys.modules, "ffmpeg_binaries", make_ffmpeg_module(ffmpeg))

    assert utils.resolve_ffmpeg_binaries_location() == bin_dir.as_posix()


def test_resolve_ffmpeg_binaries_location_rejects_incomplete_package(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    bin_dir = workspace_tmp_path / "ffmpeg"
    bin_dir.mkdir()
    ffmpeg = bin_dir / "ffmpeg.exe"
    ffmpeg.write_bytes(b"")
    monkeypatch.setitem(sys.modules, "ffmpeg_binaries", make_ffmpeg_module(ffmpeg))

    assert utils.resolve_ffmpeg_binaries_location() is None


def test_resolve_ffmpeg_binaries_location_handles_missing_package(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "ffmpeg_binaries", None)

    assert utils.resolve_ffmpeg_binaries_location() is None
