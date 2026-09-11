from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from scripts.dependency_policy import (
    BASE_IMPORTS,
    LANGUAGE_ID_IMPORTS,
    PYTORCH_PROBE,
    parse_pytorch_probe,
)
from scripts.process_logging import ProcessLogger, SetupError

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
    statement = "; ".join(
        f"import {module}" for module in BASE_IMPORTS + LANGUAGE_ID_IMPORTS
    )
    logger.run(
        [python, "-c", statement],
        "Verify core imports",
        env=env,
    )


def verify_cpu_pytorch_build(
    python: Path,
    logger: ProcessLogger,
    env: Mapping[str, str],
) -> None:
    result = logger.run(
        [python, "-c", PYTORCH_PROBE],
        "Verify CPU PyTorch build",
        env=env,
    )
    try:
        probe = parse_pytorch_probe(result.output)
    except ValueError as exc:
        raise SetupError("Unable to inspect the installed PyTorch build.") from exc
    if probe["cuda_build"] is not None:
        raise SetupError(
            "Default setup requires the CPU PyTorch build, but found "
            f"torch {probe['version']} with CUDA {probe['cuda_build']}. "
            r"Run uv sync --python 3.12 --no-dev --extra cpu."
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
