from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

from scripts import transcribe
from scripts.asr.parallel import probe_audio_duration, run_parallel_whisper_transcribe
from scripts.benchmark import BENCHMARK_AUDIO_CACHE_DIR, BENCHMARK_VIDEOS, prefetch_audio, select_videos
from scripts.config import RESULTS_DIR
from scripts.runtime_options import TranscribeOptions
from scripts.transcript_output import write_markdown
from scripts.utils import ensure_dir, read_json, write_json


def run_case(case_path: Path) -> int:
    case = read_json(case_path)
    audio_path = Path(case["audio_path"])
    output_dir = Path(case["output_dir"])
    options = TranscribeOptions(audio=audio_path, output_dir=output_dir)
    started_at = time.perf_counter()
    if case["mode"] == "serial":
        info, segments, source = transcribe.transcribe_whisper_audio(audio_path, options)
    else:
        info, segments, source = run_parallel_whisper_transcribe(
            audio_path, options, output_dir, probe_audio_duration(audio_path)
        )
    ensure_dir(output_dir)
    payload = {"bvid": audio_path.stem, "source": source, **info, "segments": segments}
    write_json(output_dir / f"{audio_path.stem}_transcript.json", payload)
    write_markdown(output_dir / f"{audio_path.stem}_transcript.md", payload)
    write_json(
        Path(case["result_path"]),
        {"mode": case["mode"], "elapsed_seconds": round(time.perf_counter() - started_at, 3), "segments": len(segments)},
    )
    return 0


def run_child(case_path: Path) -> dict:
    completed = subprocess.run([sys.executable, "-m", "scripts.benchmark_whisper_parallel", "--_case", str(case_path)])
    if completed.returncode:
        raise RuntimeError(f"Benchmark case failed: {case_path}")
    return read_json(Path(read_json(case_path)["result_path"]))


def render_markdown(results: list[dict]) -> str:
    lines = ["# Whisper Serial vs Parallel Benchmark", "", "| Video | Mode | Runs | Median |", "| --- | --- | ---: | ---: |"]
    for result in results:
        values = result["elapsed_seconds"]
        lines.append(f"| {result['bvid']} | {result['mode']} | {len(values)} | {statistics.median(values):.3f}s |")
    return "\n".join(lines) + "\n"


def run_benchmark(videos, repetitions: int, output_dir: Path) -> list[dict]:
    audio_by_bvid = prefetch_audio(videos, BENCHMARK_AUDIO_CACHE_DIR)
    grouped: dict[tuple[str, str], list[float]] = {}
    for repetition in range(1, repetitions + 1):
        for video in videos:
            for mode in ("serial", "parallel") if repetition % 2 else ("parallel", "serial"):
                case_dir = output_dir / video.bvid / f"round-{repetition}" / mode
                case_path = case_dir / "case.json"
                write_json(case_path, {"mode": mode, "audio_path": str(audio_by_bvid[video.bvid]), "output_dir": str(case_dir / "transcript"), "result_path": str(case_dir / "result.json")})
                result = run_child(case_path)
                grouped.setdefault((video.bvid, mode), []).append(result["elapsed_seconds"])
    results = [{"bvid": bvid, "mode": mode, "elapsed_seconds": values} for (bvid, mode), values in grouped.items()]
    write_json(output_dir / "benchmark.json", {"repetitions": repetitions, "results": results})
    (output_dir / "benchmark.md").write_text(render_markdown(results), encoding="utf-8")
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare serial and parallel Whisper transcription.")
    parser.add_argument("--video", action="append", choices=[video.bvid for video in BENCHMARK_VIDEOS])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR / "benchmark" / "whisper-parallel")
    parser.add_argument("--_case", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args._case:
        return run_case(args._case)
    if args.repetitions < 1:
        raise ValueError("--repetitions must be at least 1")
    run_benchmark(select_videos(args.video), args.repetitions, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
