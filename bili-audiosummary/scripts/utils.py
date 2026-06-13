import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit
from typing import Any, Optional


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(value: str, max_length: int = 120) -> str:
    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value)
    value = value.rstrip(" .")

    if not value:
        value = "untitled"

    if value.upper() in WINDOWS_RESERVED_NAMES:
        value = f"_{value}"

    if len(value) > max_length:
        value = value[:max_length].rstrip(" .")

    return value or "untitled"


def normalize_bilibili_video_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.path == "/list/watchlater/":
        bvid_values = parse_qs(parts.query).get("bvid", [])
        if bvid_values and re.fullmatch(r"BV[0-9A-Za-z]+", bvid_values[0]):
            return f"https://www.bilibili.com/video/{bvid_values[0]}/"

    match = re.search(r"/video/(BV[0-9A-Za-z]+)/?", value)
    if not match:
        return value

    normalized_path = f"/video/{match.group(1)}/"
    return urlunsplit((parts.scheme or "https", parts.netloc or "www.bilibili.com", normalized_path, "", ""))


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def path_to_posix(path: Path) -> str:
    # 把 Path 对象转换成使用 / 分隔的字符串路径
    return path.as_posix()


def list_media_files(path: Path) -> list[Path]:
    if not path.exists():
        return []

    ignored_suffixes = {".part", ".ytdl", ".tmp"}
    return sorted(
        (
            item
            for item in path.iterdir()
            if item.is_file() and item.suffix.lower() not in ignored_suffixes
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def resolve_ffmpeg_binaries_location() -> Optional[str]:
    try:
        import ffmpeg_binaries as ffmpeg
    except ImportError:
        return None

    try:
        ffmpeg.init()
        ffmpeg_path = Path(str(ffmpeg.FFMPEG_PATH))
    except Exception:
        return None

    bin_dir = ffmpeg_path.parent if ffmpeg_path.is_file() else ffmpeg_path
    exe_suffix = ".exe" if os.name == "nt" else ""
    if (bin_dir / f"ffmpeg{exe_suffix}").exists() and (bin_dir / f"ffprobe{exe_suffix}").exists():
        return path_to_posix(bin_dir)

    return None


def resolve_ffmpeg_location() -> Optional[str]:
    return resolve_ffmpeg_binaries_location()
