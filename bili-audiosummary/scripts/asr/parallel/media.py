from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts.asr.parallel.plan import ParallelAsrPlan
from scripts.utils import ensure_dir, path_to_posix, resolve_ffmpeg_location


def _ffmpeg_tool(name: str) -> str:
    location = resolve_ffmpeg_location()
    if not location:
        raise RuntimeError(
            "ffmpeg-binaries is required for parallel ASR. "
            r"Run .\scripts\setup\setup_windows.bat again to sync dependencies."
        )
    suffix = ".exe" if os.name == "nt" else ""
    return path_to_posix(Path(location) / f"{name}{suffix}")


def _run_subprocess(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def probe_audio_duration(audio_path: Path) -> float:
    output = _run_subprocess(
        [
            _ffmpeg_tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path_to_posix(audio_path),
        ]
    )
    return float(json.loads(output)["format"]["duration"])


def split_asr_chunks(audio_path: Path, plan: ParallelAsrPlan, workspace_dir: Path) -> None:
    ffmpeg = _ffmpeg_tool("ffmpeg")
    for macro in plan.macro_chunks:
        macro_dir = workspace_dir / "chunks" / f"macro_{macro.index:03d}"
        ensure_dir(macro_dir)
        for chunk in macro.chunks:
            chunk_path = workspace_dir / chunk.path
            ensure_dir(chunk_path.parent)
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{chunk.source_start:.3f}",
                "-t",
                f"{chunk.source_duration:.3f}",
                "-i",
                path_to_posix(audio_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                path_to_posix(chunk_path),
            ]
            _run_subprocess(command)
