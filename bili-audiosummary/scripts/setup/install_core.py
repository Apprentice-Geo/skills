from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

try:
    from .process_logging import ProcessLogger, SetupError
except ImportError:
    from process_logging import ProcessLogger, SetupError


OFFICIAL_PYPI_URL = "https://pypi.org/simple"
CORE_IMPORTS = ("yt_dlp", "faster_whisper", "ffmpeg_binaries")
REQUIREMENTS_CHECK_PATH = Path(__file__).with_name("requirements_check.py")

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


def _same_index(left: str, right: str) -> bool:
    return left.rstrip("/").casefold() == right.rstrip("/").casefold()


def install_requirements(
    python: Path,
    requirements_path: Path,
    logger: ProcessLogger,
    env: Mapping[str, str],
) -> None:
    command = [
        python,
        "-m",
        "pip",
        "install",
        "-r",
        requirements_path,
        "--disable-pip-version-check",
        "--progress-bar",
        "off",
    ]
    configured_index = env.get("PIP_INDEX_URL", "")
    can_retry = bool(configured_index) and not _same_index(
        configured_index,
        OFFICIAL_PYPI_URL,
    )
    result = logger.run(
        command,
        "Install requirements",
        env=env,
        check=not can_retry,
    )
    if result.returncode == 0:
        return

    print(
        "Configured pip index failed; retrying with official PyPI "
        f"({OFFICIAL_PYPI_URL})."
    )
    fallback_env = dict(env)
    fallback_env["PIP_INDEX_URL"] = OFFICIAL_PYPI_URL
    logger.run(
        command,
        "Install requirements from official PyPI",
        env=fallback_env,
    )


def verify_requirements(
    python: Path,
    requirements_path: Path,
    logger: ProcessLogger,
) -> None:
    result = logger.run(
        [python, REQUIREMENTS_CHECK_PATH, requirements_path],
        "Verify requirements",
    )
    try:
        issues = json.loads(result.output.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise SetupError("Unable to parse requirements verification output.") from exc
    if issues:
        raise SetupError(
            "Requirement verification failed:\n"
            + "\n".join(f"- {issue}" for issue in issues)
        )


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
