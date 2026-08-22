from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Any

from scripts.asr.chunking import (
    DEFAULT_VAD_PARAMETERS,
    decode_normalized_audio,
    detect_speech_samples,
)
from scripts.io_utils import read_json, sha256_file, write_json_atomic

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "sources.json"
DATA = ROOT / "data"
TARGET_MINUTES = (8, 16, 32, 64)


def safe_cut(target: int, intervals: list[tuple[int, int]], limit: int) -> int:
    for start, end in intervals:
        if start <= target < end:
            if end > limit:
                raise ValueError(
                    "No safe VAD boundary in lookahead; increase --lookahead-seconds."
                )
            return end
    return target


def packaged_ffmpeg() -> Path:
    import ffmpeg_binaries

    ffmpeg_binaries.init()
    location = Path(str(ffmpeg_binaries.FFMPEG_PATH))
    directory = location.parent if location.is_file() else location
    executable = directory / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not executable.is_file():
        raise FileNotFoundError("Packaged ffmpeg executable is missing.")
    return executable


def download(source: dict[str, Any], pin: bool) -> Path:
    raw = DATA / "raw" / source["filename"]
    expected = str(source.get("sha256") or "")
    if raw.exists():
        actual = sha256_file(raw)
        if expected and actual != expected:
            raise ValueError(f"SHA256 mismatch for {raw}")
        if not expected and not pin:
            raise ValueError("Source SHA256 is unpinned; rerun with --pin-sha256.")
        source["sha256"] = actual
        return raw
    if not expected and not pin:
        raise ValueError("Source SHA256 is unpinned; rerun with --pin-sha256.")
    raw.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=raw.parent, prefix=f".{raw.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        urllib.request.urlretrieve(source["url"], temporary)
        actual = sha256_file(temporary)
        if expected and actual != expected:
            raise ValueError(f"Downloaded SHA256 mismatch for {source['url']}")
        source["sha256"] = actual
        os.replace(temporary, raw)
    finally:
        temporary.unlink(missing_ok=True)
    return raw


def convert(raw: Path, language: str, lookahead_seconds: int) -> dict[str, Any]:
    analysis = DATA / f".{language}-analysis.wav"
    subprocess.run(
        [
            str(packaged_ffmpeg()),
            "-y",
            "-i",
            str(raw),
            "-t",
            str(64 * 60 + lookahead_seconds),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(analysis),
        ],
        check=True,
    )
    audio = decode_normalized_audio(analysis)
    intervals = detect_speech_samples(audio, DEFAULT_VAD_PARAMETERS)
    cuts: list[dict[str, Any]] = []
    try:
        with wave.open(str(analysis), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getframerate() != 16_000
                or source.getsampwidth() != 2
            ):
                raise ValueError("FFmpeg produced an invalid analysis WAV.")
            for minutes in TARGET_MINUTES:
                target = minutes * 60 * 16_000
                if source.getnframes() < target:
                    raise ValueError(
                        f"Source is shorter than the {minutes}-minute target."
                    )
                cut = safe_cut(
                    target,
                    intervals,
                    min(source.getnframes(), target + lookahead_seconds * 16_000),
                )
                output = DATA / f"{language}-{minutes}min.wav"
                source.rewind()
                frames = source.readframes(cut)
                descriptor, temporary_name = tempfile.mkstemp(
                    dir=DATA, prefix=f".{output.name}.", suffix=".tmp"
                )
                os.close(descriptor)
                temporary = Path(temporary_name)
                try:
                    with wave.open(str(temporary), "wb") as target_wave:
                        target_wave.setparams(
                            (1, 2, 16_000, 0, "NONE", "not compressed")
                        )
                        target_wave.writeframes(frames)
                    os.replace(temporary, output)
                finally:
                    temporary.unlink(missing_ok=True)
                with wave.open(str(output), "rb") as published:
                    if (
                        published.getnchannels() != 1
                        or published.getframerate() != 16_000
                        or published.getsampwidth() != 2
                        or published.getnframes() != cut
                    ):
                        raise ValueError(f"Published WAV validation failed: {output}")
                cuts.append(
                    {
                        "minutes": minutes,
                        "target_sample": target,
                        "cut_sample": cut,
                        "actual_seconds": cut / 16_000,
                        "sha256": sha256_file(output),
                    }
                )
    finally:
        analysis.unlink(missing_ok=True)
    return {"language": language, "cuts": cuts}


def prepare(pin: bool, lookahead_seconds: int) -> dict[str, Any]:
    config = read_json(SOURCES)
    summaries = []
    for source in config["sources"]:
        summaries.append(
            convert(download(source, pin), source["language"], lookahead_seconds)
        )
    if pin:
        write_json_atomic(SOURCES, config)
    manifest = {
        "sample_rate": 16_000,
        "channels": 1,
        "lookahead_seconds": lookahead_seconds,
        "sources": summaries,
    }
    write_json_atomic(DATA / "samples.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare pinned, VAD-safe benchmark audio prefixes."
    )
    parser.add_argument("--pin-sha256", action="store_true")
    parser.add_argument("--lookahead-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.lookahead_seconds < 0:
        parser.error("--lookahead-seconds must not be negative")
    prepare(args.pin_sha256, args.lookahead_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
