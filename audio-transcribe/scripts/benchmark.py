from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from scripts.transcribe import run_transcribe


def benchmark_audio(
    audio_paths: list[Path],
    *,
    provider: str,
    language: str | None,
    output_path: Path,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for audio_path in audio_paths:
        started = time.perf_counter()
        item: dict[str, Any] = {
            "audio_path": str(audio_path.resolve()),
        }
        try:
            manifest = run_transcribe(audio_path, language=language, provider=provider)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        else:
            item["manifest"] = str(manifest)
        item["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        results.append(item)
    report = {"provider": provider, "language": language, "results": results}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp"
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark transcription on ordinary local audio files."
    )
    parser.add_argument("audio_path", nargs="+", type=Path)
    parser.add_argument(
        "--provider", choices=("faster-whisper", "qwen3-asr"), required=True
    )
    parser.add_argument("--language")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results") / "benchmark.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    benchmark_audio(
        args.audio_path,
        provider=args.provider,
        language=args.language,
        output_path=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
