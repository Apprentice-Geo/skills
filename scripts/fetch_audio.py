import argparse
from pathlib import Path
from typing import Any

import subtitle_transcript
from yt_dlp import YoutubeDL

from config import (
    DEFAULT_AUDIO_CODEC,
    DEFAULT_AUDIO_SELECTOR,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    RESULTS_DIR,
    SUBTITLE_LANGUAGE_PRIORITY,
)
from utils import (
    ensure_dir,
    list_media_files,
    normalize_bilibili_video_url,
    path_to_posix,
    resolve_ffmpeg_location,
    sanitize_filename,
    write_json,
)


class CookieRequiredError(RuntimeError):
    pass


def is_http_412_error(exc: Exception) -> bool:
    message = str(exc)
    return "HTTP Error 412" in message or "HTTP 412" in message or "Precondition Failed" in message


def make_cookie_required_error() -> CookieRequiredError:
    return CookieRequiredError(
        "Bilibili returned HTTP 412, which usually means this request needs a logged-in browser cookie. "
        "Stop this run and ask the user to provide a Netscape-format cookies.txt file, then rerun with "
        "--cookies .\\cookies.txt. See README.md cookie export instructions."
    )


def parse_subtitle_langs(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_subtitle_langs(args: argparse.Namespace) -> list[str]:
    if args.subtitle_langs:
        return args.subtitle_langs
    return list(SUBTITLE_LANGUAGE_PRIORITY[args.language])


def infer_subtitle_language(path: Path) -> str:
    parts = path.name.split(".")
    if len(parts) >= 3:
        return parts[-2]
    return ""


def is_usable_subtitle(path: Path) -> bool:
    return path.suffix.lower() == ".srt"


def sort_subtitle_files(paths: list[Path], preferred_languages: list[str]) -> list[Path]:
    order = {language: index for index, language in enumerate(preferred_languages)}
    return sorted(
        paths,
        key=lambda path: (
            order.get(infer_subtitle_language(path), len(order)),
            -path.stat().st_mtime,
            path.name,
        ),
    )


def filter_subtitle_files_by_language(paths: list[Path], preferred_languages: list[str]) -> list[Path]:
    allowed_languages = set(preferred_languages)
    return [
        path
        for path in paths
        if infer_subtitle_language(path) in allowed_languages
    ]


def select_cached_subtitle_files(paths: list[Path], preferred_languages: list[str]) -> list[Path]:
    matching_language_files = filter_subtitle_files_by_language(paths, preferred_languages)
    usable_files = [path for path in matching_language_files if is_usable_subtitle(path)]
    return sort_subtitle_files(usable_files, preferred_languages)


def select_valid_srt_files(paths: list[Path], preferred_languages: list[str], context: str) -> list[Path]:
    valid_files: list[Path] = []
    for path in sort_subtitle_files(paths, preferred_languages):
        segments, error = subtitle_transcript.probe_srt(path)
        if segments is None:
            print(f"Warning: {context} subtitle is unusable: {path_to_posix(path)} ({error})")
            continue
        valid_files.append(path)
    return valid_files


def add_cookie_options(options: dict[str, Any], args: argparse.Namespace) -> None:
    if args.cookies:
        options["cookiefile"] = path_to_posix(args.cookies)

def make_base_options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {
        "noplaylist": not args.playlist,
        "retries": args.retries,
        "fragment_retries": args.retries,
        "socket_timeout": args.socket_timeout,
        "windowsfilenames": True,
        "quiet": args.quiet,
        "no_warnings": args.quiet,
    }

    ffmpeg_location = resolve_ffmpeg_location()
    if ffmpeg_location:
        options["ffmpeg_location"] = ffmpeg_location

    add_cookie_options(options, args)
    return options


def extract_metadata(url: str, args: argparse.Namespace) -> dict[str, Any]:
    options = make_base_options(args)
    options.update(
        {
            "skip_download": True,
            "writesubtitles": False,
            "writeautomaticsub": False,
        }
    )

    with YoutubeDL(options) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            if is_http_412_error(exc):
                raise make_cookie_required_error() from exc
            raise
        return ydl.sanitize_info(info)


def get_video_id(info: dict[str, Any]) -> str:
    video_id = info.get("id") or info.get("display_id") or info.get("webpage_url_basename")
    return sanitize_filename(str(video_id or "unknown-video"))


def build_result_paths(info: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    result_dir = output_dir / get_video_id(info)
    resource_dir = result_dir / "resource"

    paths = {
        "result": result_dir,
        "resource": resource_dir,
        "subtitle": resource_dir / "subtitle",
    }

    for path in (result_dir, resource_dir):
        ensure_dir(path)

    return paths


def list_current_files(path: Path, video_id: str) -> list[Path]:
    return [
        item
        for item in list_media_files(path)
        if item.name == f"{video_id}{item.suffix}" or item.name.startswith(f"{video_id}.")
    ]


def download_subtitles(
    url: str,
    subtitle_dir: Path,
    video_id: str,
    args: argparse.Namespace,
) -> list[Path]:
    ensure_dir(subtitle_dir)
    preferred_languages = resolve_subtitle_langs(args)
    cached_subtitle_files = select_valid_srt_files(
        select_cached_subtitle_files(
            list_current_files(subtitle_dir, video_id),
            preferred_languages,
        ),
        preferred_languages,
        "Cached",
    )
    if cached_subtitle_files:
        print(f"Using cached subtitle files: {len(cached_subtitle_files)}")
        return cached_subtitle_files

    cached_srt_files = select_cached_subtitle_files(
        list_current_files(subtitle_dir, video_id),
        preferred_languages,
    )
    if cached_srt_files:
        print("Warning: cached subtitle files are invalid; attempting to re-download subtitles.")

    options = make_base_options(args)
    options.update(
        {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": args.write_auto_subs,
            "subtitleslangs": preferred_languages,
            "subtitlesformat": args.subtitle_format,
            "outtmpl": path_to_posix(subtitle_dir / "%(id)s.%(ext)s"),
        }
    )

    try:
        with YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:
        if is_http_412_error(exc):
            raise make_cookie_required_error() from exc
        print(f"Warning: subtitle download failed: {exc}")

    subtitle_files = filter_subtitle_files_by_language(
        list_current_files(subtitle_dir, video_id),
        preferred_languages,
    )
    subtitle_srt_files = [path for path in subtitle_files if is_usable_subtitle(path)]
    valid_subtitle_srt_files = select_valid_srt_files(
        subtitle_srt_files,
        preferred_languages,
        "Available",
    )
    if valid_subtitle_srt_files:
        return valid_subtitle_srt_files

    non_srt_subtitle_files = [path for path in subtitle_files if not is_usable_subtitle(path)]
    return sort_subtitle_files(non_srt_subtitle_files, preferred_languages)


def download_audio(
    url: str,
    audio_dir: Path,
    video_id: str,
    args: argparse.Namespace,
) -> list[Path]:
    if args.skip_audio:
        return []

    cached_audio_files = list_current_files(audio_dir, video_id)
    if cached_audio_files:
        print(f"Using cached audio files: {len(cached_audio_files)}")
        return cached_audio_files

    options = make_base_options(args)
    options.update(
        {
            "format": args.audio_selector,
            "outtmpl": path_to_posix(audio_dir / "%(id)s.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": args.audio_format,
                    "preferredquality": args.audio_quality,
                }
            ],
        }
    )

    try:
        with YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:
        if is_http_412_error(exc):
            raise make_cookie_required_error() from exc
        print(f"Warning: audio download failed: {exc}")

    return list_current_files(audio_dir, video_id)


def compact_metadata(info: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "title",
        "fulltitle",
        "webpage_url",
        "original_url",
        "duration",
        "duration_string",
        "uploader",
        "uploader_id",
        "channel",
        "channel_id",
        "upload_date",
        "timestamp",
        "description",
        "subtitles",
        "automatic_captions",
    ]
    metadata = {key: info.get(key) for key in keys if key in info}
    for key in ("webpage_url", "original_url"):
        if metadata.get(key):
            metadata[key] = normalize_bilibili_video_url(str(metadata[key]))
    return metadata


def write_manifest(
    result_dir: Path,
    info: dict[str, Any],
    audio_files: list[Path],
    subtitle_files: list[Path],
) -> Path:
    manifest = {
        "title": info.get("title") or info.get("fulltitle") or info.get("id"),
        "id": info.get("id"),
        "url": normalize_bilibili_video_url(str(info.get("webpage_url") or info.get("original_url") or "")),
        "metadata_path": path_to_posix(result_dir / "resource" / "metadata.json"),
        "audio_files": [path_to_posix(path) for path in audio_files],
        "subtitle_files": [path_to_posix(path) for path in subtitle_files],
    }
    manifest_path = result_dir / "resource" / "fetch_manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Bilibili video metadata, subtitles, and audio with yt-dlp."
    )
    parser.add_argument("url", help="Bilibili video URL or BV URL accepted by yt-dlp.")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--cookies", type=Path, help="Path to a Netscape-format cookies.txt file.")
    parser.add_argument("--playlist", action="store_true", help="Allow playlist/multi-entry downloads.")
    parser.add_argument("--skip-audio", action="store_true")
    parser.add_argument(
        "--language",
        choices=tuple(SUBTITLE_LANGUAGE_PRIORITY.keys()),
        default=DEFAULT_TRANSCRIBE_LANGUAGE,
        help="Target language. Only subtitles from the selected language group will be requested.",
    )
    parser.add_argument("--write-auto-subs", dest="write_auto_subs", action="store_true", default=True)
    parser.add_argument("--no-write-auto-subs", dest="write_auto_subs", action="store_false")
    parser.add_argument(
        "--subtitle-langs",
        type=parse_subtitle_langs,
        default=[],
        help="Comma-separated subtitle language preference list.",
    )
    parser.add_argument("--subtitle-format", default="srt/best")
    parser.add_argument(
        "--audio-selector",
        default=DEFAULT_AUDIO_SELECTOR,
        help="yt-dlp format selector for downloading audio. Defaults to the lowest available audio stream.",
    )
    parser.add_argument("--audio-format", default=DEFAULT_AUDIO_CODEC)
    parser.add_argument("--audio-quality", default="0")
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--socket-timeout", type=int, default=30)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_fetch(args)
    except CookieRequiredError as exc:
        print(f"Error: {exc}")
        return 2
    return 0


def run_fetch(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dir(args.output_dir)

    info = extract_metadata(args.url, args)
    video_id = get_video_id(info)
    paths = build_result_paths(info, args.output_dir)

    metadata_path = paths["resource"] / "metadata.json"
    write_json(metadata_path, compact_metadata(info))

    raw_metadata_path = paths["resource"] / "metadata.raw.json"
    write_json(raw_metadata_path, info)

    subtitle_files = download_subtitles(args.url, paths["subtitle"], video_id, args)

    should_download_audio = not args.skip_audio
    audio_files = []
    if should_download_audio:
        audio_files = download_audio(args.url, paths["resource"], video_id, args)
    manifest_path = write_manifest(paths["result"], info, audio_files, subtitle_files)

    print(f"Title: {info.get('title') or info.get('id')}")
    print(f"BVID: {video_id}")
    print(f"Result: {path_to_posix(paths['result'])}")
    print(f"Metadata: {path_to_posix(metadata_path)}")
    print(f"Raw metadata: {path_to_posix(raw_metadata_path)}")
    print(f"Manifest: {path_to_posix(manifest_path)}")
    print(f"Audio files: {len(audio_files)}")
    print(f"Subtitle files: {len(subtitle_files)}")
    return {
        "info": info,
        "video_id": video_id,
        "paths": paths,
        "metadata_path": metadata_path,
        "raw_metadata_path": raw_metadata_path,
        "manifest_path": manifest_path,
        "audio_files": audio_files,
        "subtitle_files": subtitle_files,
    }


if __name__ == "__main__":
    raise SystemExit(main())
