from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from scripts.process_logging import ProcessLogger, SetupError


CORE_IMPORTS = ("yt_dlp", "faster_whisper", "ffmpeg_binaries")

FFMPEG_RESOLVER = r"""
import json
import os
import sys
from pathlib import Path

import ffmpeg_binaries as ffmpeg

ffmpeg.init()
ffmpeg_path = Path(str(ffmpeg.FFMPEG_PATH))
bin_dir = ffmpeg_path.parent if ffmpeg_path.is_file() else ffmpeg_path
suffix = ".exe" if os.name == "nt" else ""
ffmpeg_exe = bin_dir / f"ffmpeg{suffix}"
ffprobe_exe = bin_dir / f"ffprobe{suffix}"
if not ffmpeg_exe.is_file() or not ffprobe_exe.is_file():
    raise SystemExit("ffmpeg-binaries-compat did not provide ffmpeg and ffprobe")
print(json.dumps([str(ffmpeg_exe), str(ffprobe_exe)]))
"""


def verify_core_imports(
    python: Path,
    logger: ProcessLogger,
    env: Mapping[str, str],
) -> None:
    statement = "; ".join(f"import {module}" for module in CORE_IMPORTS)
    logger.run(
        [python, "-c", statement],
        "Verify core imports",
        env=env,
    )


def resolve_packaged_ffmpeg(
    python: Path,
    logger: ProcessLogger,
    env: Mapping[str, str],
) -> tuple[Path, Path]:
    result = logger.run(
        [python, "-c", FFMPEG_RESOLVER],
        "Resolve ffmpeg-binaries-compat",
        env=env,
    )
    try:
        ffmpeg, ffprobe = json.loads(result.output.strip().splitlines()[-1])
    except (IndexError, ValueError, json.JSONDecodeError) as exc:
        raise SetupError("Unable to parse packaged ffmpeg paths.") from exc
    return Path(ffmpeg), Path(ffprobe)


def verify_ffmpeg_executables(
    ffmpeg: Path,
    ffprobe: Path,
    logger: ProcessLogger,
) -> None:
    logger.run([ffmpeg, "-version"], "Verify packaged ffmpeg")
    logger.run([ffprobe, "-version"], "Verify packaged ffprobe")
