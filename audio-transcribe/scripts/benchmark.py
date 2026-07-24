from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from scripts.transcribe import run_transcribe


def benchmark_audio(
    audio_paths: list[Path],
    *,
    model: str,
    language: str | None,
    output_path: Path,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for audio_path in audio_paths:
        started = time.perf_counter()
        manifest = run_transcribe(audio_path, language=language, model=model)
        results.append(
            {
                "audio_path": str(audio_path.resolve()),
                "manifest": str(manifest),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
    report = {"model": model, "language": language, "results": results}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark transcription on ordinary local audio files."
    )
    parser.add_argument("audio_path", nargs="+", type=Path)
    parser.add_argument("--model", choices=("faster-whisper", "qwen3"), required=True)
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
        model=args.model,
        language=args.language,
        output_path=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
