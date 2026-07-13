from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterable

from scripts.asr.parallel.plan import (
    DEFAULT_VAD_PARAMETERS,
    AsrChunkPlan,
    ParallelAsrPlan,
    VadParameters,
)
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


def detect_speech_intervals(
    audio_path: Path,
    vad_parameters: VadParameters = DEFAULT_VAD_PARAMETERS,
) -> list[tuple[float, float]]:
    from faster_whisper import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(
        path_to_posix(audio_path),
        sampling_rate=vad_parameters.sampling_rate,
    )
    options = VadOptions(
        threshold=vad_parameters.threshold,
        min_speech_duration_ms=vad_parameters.min_speech_duration_ms,
        min_silence_duration_ms=vad_parameters.min_silence_duration_ms,
        speech_pad_ms=vad_parameters.speech_pad_ms,
    )
    timestamps = get_speech_timestamps(
        audio,
        options,
        sampling_rate=vad_parameters.sampling_rate,
    )
    return [
        (
            float(timestamp["start"]) / vad_parameters.sampling_rate,
            float(timestamp["end"]) / vad_parameters.sampling_rate,
        )
        for timestamp in timestamps
    ]


def split_asr_chunks(
    audio_path: Path,
    plan: ParallelAsrPlan,
    workspace_dir: Path,
    chunks: Iterable[AsrChunkPlan] | None = None,
) -> None:
    ffmpeg = _ffmpeg_tool("ffmpeg")
    selected_chunks = plan.chunks if chunks is None else list(chunks)
    for chunk in selected_chunks:
        chunk_path = workspace_dir / chunk.path
        ensure_dir(chunk_path.parent)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{chunk.start:.3f}",
            "-t",
            f"{chunk.duration:.3f}",
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
