import argparse
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL

from scripts import subtitle_transcript
from scripts.config import (
    DEFAULT_AUDIO_CODEC,
    DEFAULT_AUDIO_SELECTOR,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    RESULTS_DIR,
    SKILL_ROOT,
    SUBTITLE_LANGUAGE_PRIORITY,
)
from scripts.process_logging import (
    LoggingSession,
    YtDlpLogger,
    create_timestamped_log_path,
    get_logger,
    terminal_info,
)
from scripts.runtime_options import FetchOptions
from scripts.subtitle_utils import infer_subtitle_language
from scripts.utils import (
    ensure_dir,
    list_media_files,
    normalize_bilibili_video_url,
    path_to_posix,
    resolve_ffmpeg_location,
    sanitize_filename,
    write_json,
)

logger = get_logger(__name__)


class CookieRequiredError(RuntimeError):
    pass


DEFAULT_COOKIE_FILENAMES = [
    "cookies.txt",
    "www.bilibili.com_cookies.txt",
    "bilibili_cookies.txt",
]


def is_http_412_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "HTTP Error 412" in message
        or "HTTP 412" in message
        or "Precondition Failed" in message
    )


def make_cookie_required_error() -> CookieRequiredError:
    return CookieRequiredError(
        "Bilibili returned HTTP 412, which usually means this request needs a logged-in browser cookie. "
        "Stop this run and ask the user to provide a Netscape-format cookies.txt file, then rerun with "
        "--cookies .\\cookies.txt. See README.md cookie export instructions."
    )


def parse_subtitle_langs(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_subtitle_langs(options: FetchOptions) -> list[str]:
    if options.subtitle_langs:
        return options.subtitle_langs
    return list(SUBTITLE_LANGUAGE_PRIORITY[options.language])


def resolve_cookie_path(options: FetchOptions) -> Path | None:
    if options.cookies:
        return options.cookies

    for filename in DEFAULT_COOKIE_FILENAMES:
        candidate = SKILL_ROOT / filename
        if candidate.is_file():
            return candidate

    return None


def is_usable_subtitle(path: Path) -> bool:
    return path.suffix.lower() == ".srt"


def sort_subtitle_files(
    paths: list[Path], preferred_languages: list[str]
) -> list[Path]:
    order = {language: index for index, language in enumerate(preferred_languages)}

    def sort_key(path: Path) -> tuple[int, float, str]:
        language = infer_subtitle_language(path)
        language_order = (
            order.get(language, len(order)) if language is not None else len(order)
        )
        return language_order, -path.stat().st_mtime, path.name

    return sorted(
        paths,
        key=sort_key,
    )


def filter_subtitle_files_by_language(
    paths: list[Path], preferred_languages: list[str]
) -> list[Path]:
    allowed_languages = set(preferred_languages)
    return [
        path for path in paths if infer_subtitle_language(path) in allowed_languages
    ]


def select_cached_subtitle_files(
    paths: list[Path], preferred_languages: list[str]
) -> list[Path]:
    matching_language_files = filter_subtitle_files_by_language(
        paths, preferred_languages
    )
    usable_files = [
        path for path in matching_language_files if is_usable_subtitle(path)
    ]
    return sort_subtitle_files(usable_files, preferred_languages)


def select_valid_srt_files(
    paths: list[Path], preferred_languages: list[str], context: str
) -> list[Path]:
    valid_files: list[Path] = []
    for path in sort_subtitle_files(paths, preferred_languages):
        segments, error = subtitle_transcript.probe_srt(path)
        if segments is None:
            logger.warning(
                "%s subtitle is unusable: %s (%s)",
                context,
                path_to_posix(path),
                error,
            )
            continue
        valid_files.append(path)
    return valid_files


def add_cookie_options(ydl_options: dict[str, Any], options: FetchOptions) -> None:
    cookie_path = resolve_cookie_path(options)
    if cookie_path:
        ydl_options["cookiefile"] = path_to_posix(cookie_path)


def make_base_options(options: FetchOptions) -> dict[str, Any]:
    ydl_options: dict[str, Any] = {
        "noplaylist": not options.playlist,
        "retries": options.retries,
        "fragment_retries": options.retries,
        "socket_timeout": options.socket_timeout,
        "windowsfilenames": True,
        "quiet": options.quiet,
        "no_warnings": False,
        "logger": YtDlpLogger(logger),
    }

    ffmpeg_location = resolve_ffmpeg_location()
    if not ffmpeg_location:
        raise RuntimeError(
            "ffmpeg-binaries-compat is unavailable. Run "
            r".\scripts\setup\setup_windows.bat to repair the environment."
        )
    ydl_options["ffmpeg_location"] = ffmpeg_location

    add_cookie_options(ydl_options, options)
    return ydl_options


def extract_metadata(url: str, options: FetchOptions) -> dict[str, Any]:
    ydl_options = make_base_options(options)
    ydl_options.update(
        {
            "skip_download": True,
            "writesubtitles": False,
            "writeautomaticsub": False,
        }
    )

    with YoutubeDL(cast(Any, ydl_options)) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            if is_http_412_error(exc):
                raise make_cookie_required_error() from exc
            raise
        if info is None:
            raise RuntimeError("yt-dlp returned no metadata.")
        sanitized = ydl.sanitize_info(info)
        if sanitized is None:
            raise RuntimeError("yt-dlp returned no usable metadata.")
        return dict(sanitized)


def get_video_id(info: dict[str, Any]) -> str:
    video_id = (
        info.get("id") or info.get("display_id") or info.get("webpage_url_basename")
    )
    return sanitize_filename(str(video_id or "unknown-video"))


def build_canonical_url(info: dict[str, Any], video_id: str) -> str:
    source_url = str(info.get("webpage_url") or info.get("original_url") or "")
    bvid_match = re.search(r"/video/(BV[0-9A-Za-z]+)", source_url)
    bvid = bvid_match.group(1) if bvid_match else video_id.split("_p", 1)[0]

    page_match = re.search(r"_p([0-9]+)$", str(info.get("id") or video_id))
    query_page = parse_qs(urlsplit(source_url).query).get("p", [])
    page = page_match.group(1) if page_match else (query_page[0] if query_page else "")
    page_suffix = f"?p={page}" if page else ""
    return f"https://www.bilibili.com/video/{bvid}/{page_suffix}"


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
        if item.name == f"{video_id}{item.suffix}"
        or item.name.startswith(f"{video_id}.")
    ]


def download_subtitles(
    url: str,
    subtitle_dir: Path,
    video_id: str,
    options: FetchOptions,
) -> list[Path]:
    ensure_dir(subtitle_dir)
    preferred_languages = resolve_subtitle_langs(options)
    cached_subtitle_files = select_valid_srt_files(
        select_cached_subtitle_files(
            list_current_files(subtitle_dir, video_id),
            preferred_languages,
        ),
        preferred_languages,
        "Cached",
    )
    if cached_subtitle_files:
        logger.info("Using cached subtitle files: %d", len(cached_subtitle_files))
        return cached_subtitle_files

    cached_srt_files = select_cached_subtitle_files(
        list_current_files(subtitle_dir, video_id),
        preferred_languages,
    )
    if cached_srt_files:
        logger.warning(
            "Warning: cached subtitle files are invalid; attempting to re-download subtitles."
        )

    ydl_options = make_base_options(options)
    ydl_options.update(
        {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": options.write_auto_subs,
            "subtitleslangs": preferred_languages,
            "subtitlesformat": options.subtitle_format,
            "outtmpl": path_to_posix(subtitle_dir / "%(id)s.%(ext)s"),
        }
    )

    try:
        with YoutubeDL(cast(Any, ydl_options)) as ydl:
            ydl.download([url])
    except Exception as exc:
        if is_http_412_error(exc):
            raise make_cookie_required_error() from exc
        logger.warning("Subtitle download failed: %s", exc, exc_info=True)

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

    non_srt_subtitle_files = [
        path for path in subtitle_files if not is_usable_subtitle(path)
    ]
    return sort_subtitle_files(non_srt_subtitle_files, preferred_languages)


def download_audio(
    url: str,
    audio_dir: Path,
    video_id: str,
    options: FetchOptions,
) -> list[Path]:
    if options.skip_audio:
        return []

    cached_audio_files = list_current_files(audio_dir, video_id)
    if cached_audio_files:
        logger.info("Using cached audio files: %d", len(cached_audio_files))
        return cached_audio_files

    ydl_options = make_base_options(options)
    ydl_options.update(
        {
            "format": options.audio_selector,
            "outtmpl": path_to_posix(audio_dir / "%(id)s.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": options.audio_format,
                    "preferredquality": options.audio_quality,
                }
            ],
        }
    )

    try:
        with YoutubeDL(cast(Any, ydl_options)) as ydl:
            ydl.download([url])
    except Exception as exc:
        if is_http_412_error(exc):
            raise make_cookie_required_error() from exc
        logger.warning("Audio download failed: %s", exc, exc_info=True)

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
    canonical_url: str,
) -> Path:
    manifest = {
        "title": info.get("title") or info.get("fulltitle") or info.get("id"),
        "id": info.get("id"),
        "url": canonical_url,
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
    parser.add_argument(
        "--cookies", type=Path, help="Path to a Netscape-format cookies.txt file."
    )
    parser.add_argument(
        "--playlist", action="store_true", help="Allow playlist/multi-entry downloads."
    )
    parser.add_argument("--skip-audio", action="store_true")
    parser.add_argument(
        "--skip-subtitles", action="store_true", help="Skip subtitle reuse/download."
    )
    parser.add_argument(
        "--language",
        choices=tuple(SUBTITLE_LANGUAGE_PRIORITY.keys()),
        default=DEFAULT_TRANSCRIBE_LANGUAGE,
        help="Target language. Only subtitles from the selected language group will be requested.",
    )
    parser.add_argument(
        "--write-auto-subs", dest="write_auto_subs", action="store_true", default=True
    )
    parser.add_argument(
        "--no-write-auto-subs", dest="write_auto_subs", action="store_false"
    )
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
    options = FetchOptions.from_args(parse_args())
    log_path = create_timestamped_log_path(SKILL_ROOT / ".cache" / "logs", "fetch")
    with LoggingSession(log_path) as session:
        try:
            run_fetch(options)
        except Exception as exc:
            session.report_failure(exc)
            return 2 if isinstance(exc, CookieRequiredError) else 1
    return 0


def run_fetch(args: argparse.Namespace | FetchOptions) -> dict[str, Any]:
    options = FetchOptions.from_args(args)
    ensure_dir(options.output_dir)
    normalized_url = normalize_bilibili_video_url(options.url)
    terminal_info(logger, "[Stage] Fetch metadata, subtitles, and audio")
    terminal_info(logger, "[BiliBili] Extracting URL: %s", normalized_url)

    cookie_path = resolve_cookie_path(options)
    if cookie_path and not options.cookies:
        logger.info("Using auto-detected cookies: %s", path_to_posix(cookie_path))

    info = extract_metadata(normalized_url, options)
    video_id = get_video_id(info)
    canonical_url = build_canonical_url(info, video_id)
    paths = build_result_paths(info, options.output_dir)
    session = LoggingSession.current()
    if session is not None:
        session.move_to(paths["result"])

    metadata_path = paths["resource"] / "metadata.json"
    write_json(metadata_path, compact_metadata(info))

    raw_metadata_path = paths["resource"] / "metadata.raw.json"
    write_json(raw_metadata_path, info)

    subtitle_files = []
    if not options.skip_subtitles:
        subtitle_files = download_subtitles(
            canonical_url, paths["subtitle"], video_id, options
        )

    should_download_audio = not options.skip_audio
    audio_files = []
    if should_download_audio:
        audio_files = download_audio(
            canonical_url, paths["resource"], video_id, options
        )
    manifest_path = write_manifest(
        paths["result"], info, audio_files, subtitle_files, canonical_url
    )

    terminal_info(logger, "Title: %s", info.get("title") or info.get("id"))
    logger.info("BVID: %s", video_id)
    logger.info("Canonical URL: %s", canonical_url)
    logger.info("Result: %s", path_to_posix(paths["result"]))
    logger.info("Metadata: %s", path_to_posix(metadata_path))
    logger.info("Raw metadata: %s", path_to_posix(raw_metadata_path))
    logger.info("Manifest: %s", path_to_posix(manifest_path))
    logger.info("Audio files: %d", len(audio_files))
    logger.info("Subtitle files: %d", len(subtitle_files))
    return {
        "info": info,
        "video_id": video_id,
        "paths": paths,
        "metadata_path": metadata_path,
        "raw_metadata_path": raw_metadata_path,
        "manifest_path": manifest_path,
        "audio_files": audio_files,
        "subtitle_files": subtitle_files,
        "log_path": session.log_path if session is not None else None,
    }


if __name__ == "__main__":
    raise SystemExit(main())
