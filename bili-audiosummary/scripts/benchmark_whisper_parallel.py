from __future__ import annotations

import argparse
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from scripts.asr.parallel import run_parallel_whisper_transcribe
from scripts.benchmark import (
    BENCHMARK_AUDIO_CACHE_DIR,
    BENCHMARK_VIDEOS,
    prefetch_audio,
    require_provider_ready,
    select_videos,
)
from scripts.config import RESULTS_DIR
from scripts.runtime_options import TranscribeOptions
from scripts.utils import ensure_dir, read_json, write_json


DEFAULT_MAX_CHUNK_SECONDS = (180, 300, 450)
DEFAULT_VIDEO_IDS = (
    "BV1W694BEE7F",
    "BV1Nt4y1D7pW",
    "BV1MN4y177PB",
    "BV1ks411e7W4",
)


def rotated_limits(limits: tuple[int, ...], repetition_index: int) -> tuple[int, ...]:
    offset = repetition_index % len(limits)
    return (*limits[offset:], *limits[:offset])


def run_case(case_path: Path) -> int:
    case = read_json(case_path)
    audio_path = Path(case["audio_path"])
    output_dir = Path(case["output_dir"])
    options = TranscribeOptions(
        audio=audio_path,
        output_dir=output_dir,
        max_chunk_seconds=float(case["max_chunk_seconds"]),
    )
    started_at = time.perf_counter()
    run_parallel_whisper_transcribe(
        audio_path,
        options,
        output_dir / "asr_parallel",
    )
    elapsed = round(time.perf_counter() - started_at, 3)
    metrics = read_json(output_dir / "asr_parallel" / "metrics.json")
    write_json(
        Path(case["result_path"]),
        {
            "elapsed_seconds": elapsed,
            **{
                key: metrics[key]
                for key in (
                    "chunk_count",
                    "num_workers",
                    "batch_count",
                    "hard_cut_count",
                    "chunk_estimated_speech_durations",
                    "max_estimated_speech_duration",
                    "speech_load_msre",
                )
            },
        },
    )
    return 0


def run_child(case_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.benchmark_whisper_parallel", "--_case", str(case_path)]
    )
    if completed.returncode:
        raise RuntimeError(f"Benchmark case failed: {case_path}")
    return read_json(Path(read_json(case_path)["result_path"]))


def _median_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: item["elapsed_seconds"])
    middle = ordered[len(ordered) // 2]
    return {
        **{key: value for key, value in middle.items() if key != "repetition"},
        "elapsed_seconds": statistics.median(
            item["elapsed_seconds"] for item in records
        ),
        "elapsed_samples": [item["elapsed_seconds"] for item in records],
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(
            (str(result["bvid"]), int(result["max_chunk_seconds"])), []
        ).append(result)
    medians = [
        {
            "bvid": bvid,
            "max_chunk_seconds": limit,
            **_median_record(records),
        }
        for (bvid, limit), records in sorted(grouped.items())
    ]
    videos = sorted({item["bvid"] for item in medians})
    limits = sorted({item["max_chunk_seconds"] for item in medians})
    by_key = {
        (item["bvid"], item["max_chunk_seconds"]): item for item in medians
    }
    if 300 not in limits:
        raise ValueError("Chunk-limit comparison requires a 300-second reference.")
    minimum_hard_cuts = {
        bvid: min(by_key[(bvid, limit)]["hard_cut_count"] for limit in limits)
        for bvid in videos
    }
    quality_pass = {
        str(limit): all(
            by_key[(bvid, limit)]["hard_cut_count"] == minimum_hard_cuts[bvid]
            for bvid in videos
        )
        for limit in limits
    }
    geometric_ratios: dict[str, float] = {}
    for limit in limits:
        ratios = [
            by_key[(bvid, limit)]["elapsed_seconds"]
            / by_key[(bvid, 300)]["elapsed_seconds"]
            for bvid in videos
        ]
        geometric_ratios[str(limit)] = math.prod(ratios) ** (1 / len(ratios))
    eligible = [limit for limit in limits if quality_pass[str(limit)]]
    winner = min(eligible, key=lambda limit: (geometric_ratios[str(limit)], limit))
    return {
        "medians": medians,
        "minimum_hard_cuts_by_video": minimum_hard_cuts,
        "quality_pass": quality_pass,
        "geometric_mean_runtime_ratio_to_300": geometric_ratios,
        "winner_max_chunk_seconds": winner,
    }


def load_schema4_baseline(path: Path) -> dict[str, float]:
    samples: dict[str, list[float]] = {}
    for report_path in path.glob("*/benchmark.json"):
        for result in read_json(report_path).get("results", []):
            if result.get("provider") == "whisper" and result.get("status") == "ok":
                samples.setdefault(result["bvid"], []).append(result["elapsed_seconds"])
    return {bvid: statistics.median(values) for bvid, values in samples.items()}


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Whisper Chunk Limit Benchmark",
        "",
        f"Winner: `{summary['winner_max_chunk_seconds']}s`",
        "",
        "| Video | Limit | Runs | Median | Chunks | Workers | Batches | Hard cuts | Max speech | Speech MSRE |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["medians"]:
        lines.append(
            "| {bvid} | {max_chunk_seconds}s | {runs} | {elapsed_seconds:.3f}s | "
            "{chunk_count} | {num_workers} | {batch_count} | {hard_cut_count} | "
            "{max_estimated_speech_duration:.3f}s | {speech_load_msre:.6f} |".format(
                runs=len(item["elapsed_samples"]), **item
            )
        )
    lines.extend(
        [
            "",
            "| Limit | Hard-cut gate | Geometric mean vs 300s |",
            "| ---: | --- | ---: |",
        ]
    )
    for limit, ratio in summary["geometric_mean_runtime_ratio_to_300"].items():
        lines.append(
            f"| {limit}s | {'pass' if summary['quality_pass'][limit] else 'fail'} | {ratio:.6f} |"
        )
    if report.get("winner_vs_schema4"):
        lines.extend(["", "## Winner vs Schema 4", ""])
        for bvid, ratio in report["winner_vs_schema4"].items():
            lines.append(f"- `{bvid}`: `{ratio:.6f}x`")
    return "\n".join(lines) + "\n"


def run_benchmark(
    videos,
    repetitions: int,
    limits: tuple[int, ...],
    output_dir: Path,
    baseline_dir: Path | None = None,
) -> dict[str, Any]:
    require_provider_ready("whisper")
    audio_by_bvid = prefetch_audio(videos, BENCHMARK_AUDIO_CACHE_DIR)
    run_dir = ensure_dir(output_dir / datetime.now().strftime("%Y%m%d-%H%M%S"))
    results: list[dict[str, Any]] = []
    for repetition_index in range(repetitions):
        for video in videos:
            for limit in rotated_limits(limits, repetition_index):
                case_dir = run_dir / video.bvid / f"round-{repetition_index + 1}" / f"max-{limit}"
                case_path = case_dir / "case.json"
                write_json(
                    case_path,
                    {
                        "audio_path": str(audio_by_bvid[video.bvid]),
                        "output_dir": str(case_dir / "transcript"),
                        "result_path": str(case_dir / "result.json"),
                        "max_chunk_seconds": limit,
                    },
                )
                result = run_child(case_path)
                results.append(
                    {
                        "bvid": video.bvid,
                        "repetition": repetition_index + 1,
                        "max_chunk_seconds": limit,
                        **result,
                    }
                )
    summary = summarize_results(results)
    report: dict[str, Any] = {
        "repetitions": repetitions,
        "limits": list(limits),
        "results": results,
        "summary": summary,
    }
    if baseline_dir is not None:
        baseline = load_schema4_baseline(baseline_dir)
        winner = summary["winner_max_chunk_seconds"]
        winner_medians = {
            item["bvid"]: item["elapsed_seconds"]
            for item in summary["medians"]
            if item["max_chunk_seconds"] == winner
        }
        report["schema4_baseline_medians"] = baseline
        report["winner_vs_schema4"] = {
            bvid: winner_medians[bvid] / elapsed
            for bvid, elapsed in baseline.items()
            if bvid in winner_medians
        }
    write_json(run_dir / "benchmark.json", report)
    (run_dir / "benchmark.md").write_text(_render_markdown(report), encoding="utf-8")
    return report


def _default_videos():
    wanted = set(DEFAULT_VIDEO_IDS)
    return tuple(video for video in BENCHMARK_VIDEOS if video.bvid in wanted)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Whisper chunk upper limits.")
    parser.add_argument(
        "--video", action="append", choices=[video.bvid for video in BENCHMARK_VIDEOS]
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--max-chunk-seconds",
        type=int,
        action="append",
        default=None,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=RESULTS_DIR / "benchmark" / "whisper-chunk-limits",
    )
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--_case", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.max_chunk_seconds = args.max_chunk_seconds or list(DEFAULT_MAX_CHUNK_SECONDS)
    return args


def main() -> int:
    args = parse_args()
    if args._case:
        return run_case(args._case)
    if args.repetitions < 1:
        raise ValueError("--repetitions must be at least 1")
    limits = tuple(args.max_chunk_seconds)
    if len(set(limits)) != len(limits) or any(limit < 30 for limit in limits):
        raise ValueError("Chunk limits must be unique integers of at least 30 seconds.")
    videos = select_videos(args.video) if args.video else _default_videos()
    run_benchmark(videos, args.repetitions, limits, args.output_dir, args.baseline_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
