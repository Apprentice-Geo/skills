from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import fetch_audio, transcribe
from scripts.asr.qwen3 import has_model_weights
from scripts.config import (
    DEFAULT_TRANSCRIBE_LANGUAGE,
    DEFAULT_TRANSCRIBE_DEVICE,
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_BEAM_SIZE,
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ASR_MODEL_DIR,
    RESULTS_DIR,
    SKILL_ROOT,
)
from scripts.runtime_options import FetchOptions, TranscribeOptions
from scripts.utils import ensure_dir, read_json, write_json


POLL_INTERVAL_SECONDS = 0.1
DEFAULT_PROVIDERS = ("whisper", "qwen3")
BENCHMARK_AUDIO_CACHE_DIR = SKILL_ROOT / ".cache" / "benchmark" / "audio"


@dataclass(frozen=True)
class BenchmarkVideo:
    bvid: str
    duration_label: str

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}/"


# Benchmark videos and their supplied durations in HH:MM:SS format.
BENCHMARK_VIDEOS = (
      
    BenchmarkVideo("BV1W694BEE7F", "00:01:03"), 
    BenchmarkVideo("BV1qt411j7fV", "00:03:41"), 
    BenchmarkVideo("BV1Ls41127sG", "00:05:57"),
    BenchmarkVideo("BV1Nt4y1D7pW", "00:07:56"), 
    BenchmarkVideo("BV1MN4y177PB", "00:11:27"),
    BenchmarkVideo("BV1ks411e7W4", "00:19:45"),  
    BenchmarkVideo("BV1Fa411c7Vh", "00:30:23"),  
    BenchmarkVideo("BV1rb4y1D7Gf", "00:39:51"),  
    BenchmarkVideo("BV1jJ411r7EL", "01:02:23"), 
    BenchmarkVideo("BV1e24y1D7qt", "01:47:01"), 
    BenchmarkVideo("BV1mL411z7Kf", "02:59:43"),
 
)


def make_fetch_options(video: BenchmarkVideo, cache_dir: Path) -> FetchOptions:
    return FetchOptions(
        url=video.url,
        output_dir=cache_dir,
        skip_audio=False,
        skip_subtitles=True,
        language=DEFAULT_TRANSCRIBE_LANGUAGE,
        quiet=True,
    )


def require_provider_ready(provider: str) -> None:
    if provider == "whisper":
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("faster-whisper dependencies are not installed. Run .\\scripts\\setup\\setup_windows.bat.") from exc
        transcribe.default_model_path()
        return

    if provider != "qwen3":
        raise ValueError(f"Unsupported ASR provider: {provider}")

    try:
        import torch
        from qwen_asr import Qwen3ASRModel  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Qwen3 ASR dependencies are not installed. Run "
            "uv sync --python 3.12 --no-dev --extra qwen3."
        ) from exc
    if not torch.cuda.is_available():
        raise RuntimeError("Qwen3 ASR requires an available CUDA GPU.")
    if not has_model_weights(QWEN3_ASR_MODEL_DIR) or not has_model_weights(QWEN3_ALIGNER_MODEL_DIR):
        raise RuntimeError(
            "Qwen3 local models are missing. Run "
            "uv run --no-sync python -m scripts.setup.install_model --model qwen3."
        )


def require_psutil() -> Any:
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError(
            "Benchmark requires psutil. Re-run the project setup to install dependencies."
        ) from exc
    return psutil


def prefetch_audio(videos: tuple[BenchmarkVideo, ...], cache_dir: Path) -> dict[str, Path]:
    audio_by_bvid: dict[str, Path] = {}
    for video in videos:
        result = fetch_audio.run_fetch(make_fetch_options(video, cache_dir))
        audio_files = result["audio_files"]
        if not audio_files:
            raise RuntimeError(f"No audio was fetched for {video.bvid}.")
        audio_by_bvid[video.bvid] = Path(audio_files[0])
    return audio_by_bvid


def probe_audio_duration(audio_path: Path) -> float | None:
    try:
        return float(transcribe.parallel_asr.probe_audio_duration(audio_path))
    except Exception:
        return None


def _cuda_peak_metrics(provider: str) -> dict[str, int | None]:
    if provider != "qwen3":
        return {
            "cuda_peak_allocated_bytes": None,
            "cuda_peak_reserved_bytes": None,
        }

    try:
        import torch

        torch.cuda.synchronize()
        return {
            "cuda_peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "cuda_peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
        }
    except Exception:
        return {
            "cuda_peak_allocated_bytes": None,
            "cuda_peak_reserved_bytes": None,
        }


def run_case(case_path: Path) -> int:
    case = read_json(case_path)
    result_path = Path(case["result_path"])
    provider = str(case["provider"])
    try:
        if provider == "qwen3":
            import torch

            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()

        started_at = time.perf_counter()
        transcribe.run_transcribe(
            TranscribeOptions(
                audio=Path(case["audio_path"]),
                output_dir=Path(case["transcript_dir"]),
                asr_provider=provider,
                language=DEFAULT_TRANSCRIBE_LANGUAGE,
                device=DEFAULT_TRANSCRIBE_DEVICE,
                compute_type=DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
                beam_size=DEFAULT_TRANSCRIBE_BEAM_SIZE,
            )
        )
        if provider == "qwen3":
            import torch

            torch.cuda.synchronize()
        result = {
            "status": "ok",
            "elapsed_seconds": round(time.perf_counter() - started_at, 3),
            **_cuda_peak_metrics(provider),
        }
        write_json(result_path, result)
        return 0
    except Exception as exc:
        result = {
            "status": "failed",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            **_cuda_peak_metrics(provider),
        }
        write_json(result_path, result)
        return 1


def process_tree_rss_bytes(process: Any) -> int:
    try:
        processes = [process, *process.children(recursive=True)]
    except Exception:
        return 0

    total = 0
    for item in processes:
        try:
            total += int(item.memory_info().rss)
        except Exception:
            continue
    return total


def run_case_process(case_path: Path, log_path: Path) -> dict[str, Any]:
    psutil = require_psutil()

    ensure_dir(log_path.parent)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "scripts.benchmark", "--_case", str(case_path)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=SKILL_ROOT,
        )
        monitored_process = psutil.Process(process.pid)
        peak_rss_bytes = 0
        while process.poll() is None:
            peak_rss_bytes = max(peak_rss_bytes, process_tree_rss_bytes(monitored_process))
            time.sleep(POLL_INTERVAL_SECONDS)
        peak_rss_bytes = max(peak_rss_bytes, process_tree_rss_bytes(monitored_process))
        returncode = process.wait()

    result_path = Path(read_json(case_path)["result_path"])
    if result_path.is_file():
        result = read_json(result_path)
    else:
        result = {
            "status": "failed",
            "error": f"Benchmark child exited with code {returncode} before writing a result.",
            "cuda_peak_allocated_bytes": None,
            "cuda_peak_reserved_bytes": None,
        }
    result["peak_rss_bytes"] = peak_rss_bytes
    result["returncode"] = returncode
    result["log_path"] = str(log_path)
    return result


def format_mebibytes(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value / (1024 * 1024):.1f} MiB"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ASR Benchmark",
        "",
        f"Started: {report['started_at']}",
        "",
        "Measured interval includes model loading, transcription, alignment, and transcript writing. "
        "Audio fetches, dependency installation, and model downloads are excluded.",
        "",
        "Peak RSS is a sampled aggregate across the benchmark child process and its descendants. "
        "CUDA metrics are reported only for Qwen3.",
        "",
        "| Video | Duration | Provider | Status | Time | RTF | Peak RSS | CUDA allocated | CUDA reserved |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in report["results"]:
        elapsed = result.get("elapsed_seconds")
        duration = result.get("audio_duration_seconds")
        rtf = "-" if elapsed is None or not duration else f"{elapsed / duration:.2f}x"
        time_text = "-" if elapsed is None else f"{elapsed:.2f}s"
        lines.append(
            "| {bvid} | {duration_label} | {provider} | {status} | {time_text} | {rtf} | {rss} | {allocated} | {reserved} |".format(
                bvid=result["bvid"],
                duration_label=result["duration_label"],
                provider=result["provider"],
                status=result["status"],
                time_text=time_text,
                rtf=rtf,
                rss=format_mebibytes(result.get("peak_rss_bytes")),
                allocated=format_mebibytes(result.get("cuda_peak_allocated_bytes")),
                reserved=format_mebibytes(result.get("cuda_peak_reserved_bytes")),
            )
        )
    return "\n".join(lines) + "\n"


def select_videos(selected_bvids: list[str] | None) -> tuple[BenchmarkVideo, ...]:
    if not selected_bvids:
        return BENCHMARK_VIDEOS
    selected = set(selected_bvids)
    known = {video.bvid for video in BENCHMARK_VIDEOS}
    unknown = selected - known
    if unknown:
        raise ValueError(f"Unknown benchmark video: {', '.join(sorted(unknown))}")
    return tuple(video for video in BENCHMARK_VIDEOS if video.bvid in selected)


def create_run_dir(output_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_dir / timestamp
    if run_dir.exists():
        raise RuntimeError(f"Benchmark output directory already exists: {run_dir}")
    return ensure_dir(run_dir)


def run_benchmark(
    videos: tuple[BenchmarkVideo, ...],
    providers: tuple[str, ...],
    output_dir: Path,
) -> dict[str, Any]:
    require_psutil()
    for provider in providers:
        require_provider_ready(provider)

    audio_by_bvid = prefetch_audio(videos, BENCHMARK_AUDIO_CACHE_DIR)
    run_dir = create_run_dir(output_dir)
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "results": [],
    }
    for video in videos:
        audio_path = audio_by_bvid[video.bvid]
        audio_duration_seconds = probe_audio_duration(audio_path)
        for provider in providers:
            case_dir = run_dir / video.bvid / provider
            result_path = case_dir / "case_result.json"
            case_path = case_dir / "case.json"
            write_json(
                case_path,
                {
                    "provider": provider,
                    "audio_path": str(audio_path),
                    "transcript_dir": str(case_dir / "transcript"),
                    "result_path": str(result_path),
                },
            )
            result = run_case_process(case_path, case_dir / "transcribe.log")
            report["results"].append(
                {
                    "bvid": video.bvid,
                    "url": video.url,
                    "duration_label": video.duration_label,
                    "audio_duration_seconds": audio_duration_seconds,
                    "provider": provider,
                    **result,
                }
            )

    write_json(run_dir / "benchmark.json", report)
    (run_dir / "benchmark.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark local ASR transcription time and memory.")
    parser.add_argument("--video", action="append", choices=[video.bvid for video in BENCHMARK_VIDEOS])
    parser.add_argument("--provider", action="append", choices=DEFAULT_PROVIDERS)
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR / "benchmark")
    parser.add_argument("--_case", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args._case:
        return run_case(args._case)
    try:
        report = run_benchmark(
            select_videos(args.video),
            tuple(args.provider or DEFAULT_PROVIDERS),
            args.output_dir,
        )
    except Exception as exc:
        print(f"Benchmark setup failed: {exc}", file=sys.stderr)
        return 1

    failed = [result for result in report["results"] if result["status"] != "ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
