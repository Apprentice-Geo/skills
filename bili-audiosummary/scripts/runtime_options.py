from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from scripts.config import (
    DEFAULT_AUDIO_CODEC,
    DEFAULT_AUDIO_SELECTOR,
    DEFAULT_SUBTITLE_LANGUAGE,
    RESULTS_DIR,
)


@dataclass
class FetchOptions:
    url: str
    output_dir: Path = RESULTS_DIR
    cookies: Path | None = None
    playlist: bool = False
    skip_audio: bool = False
    skip_subtitles: bool = False
    language: str = DEFAULT_SUBTITLE_LANGUAGE
    write_auto_subs: bool = True
    subtitle_langs: list[str] = field(default_factory=list)
    subtitle_format: str = "srt/best"
    audio_selector: str = DEFAULT_AUDIO_SELECTOR
    audio_format: str = DEFAULT_AUDIO_CODEC
    audio_quality: str = "0"
    retries: int = 10
    socket_timeout: int = 30
    quiet: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace | FetchOptions) -> FetchOptions:
        if isinstance(args, cls):
            return args
        return cls(
            url=args.url,
            output_dir=getattr(args, "output_dir", RESULTS_DIR),
            cookies=getattr(args, "cookies", None),
            playlist=getattr(args, "playlist", False),
            skip_audio=getattr(args, "skip_audio", False),
            skip_subtitles=getattr(args, "skip_subtitles", False),
            language=getattr(args, "language", DEFAULT_SUBTITLE_LANGUAGE),
            write_auto_subs=getattr(args, "write_auto_subs", True),
            subtitle_langs=list(getattr(args, "subtitle_langs", []) or []),
            subtitle_format=getattr(args, "subtitle_format", "srt/best"),
            audio_selector=getattr(args, "audio_selector", DEFAULT_AUDIO_SELECTOR),
            audio_format=getattr(args, "audio_format", DEFAULT_AUDIO_CODEC),
            audio_quality=getattr(args, "audio_quality", "0"),
            retries=getattr(args, "retries", 10),
            socket_timeout=getattr(args, "socket_timeout", 30),
            quiet=getattr(args, "quiet", False),
        )


@dataclass
class PipelineOptions:
    url: str
    cookies: Path | None = None
    language: str = DEFAULT_SUBTITLE_LANGUAGE
    summary_language: str | None = None
    skip_subtitles: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace | PipelineOptions) -> PipelineOptions:
        if isinstance(args, cls):
            return args
        return cls(
            url=args.url,
            cookies=getattr(args, "cookies", None),
            language=getattr(args, "language", DEFAULT_SUBTITLE_LANGUAGE),
            summary_language=getattr(args, "summary_language", None),
            skip_subtitles=getattr(args, "skip_subtitles", False),
        )
