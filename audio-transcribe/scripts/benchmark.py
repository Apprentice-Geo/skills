from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import wave
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import psutil

from scripts.io_utils import read_json, write_json_atomic
from scripts.model_identity import MODEL_REVISIONS

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "benchmark" / "data"
TMP_DIR = ROOT / "benchmark" / "tmp"
MODES = ("project-slicing", "provider-native")
PROVIDERS = ("faster-whisper", "qwen3-asr")
LANGUAGES = ("zh", "en")
MINUTES = (8, 16, 32, 64)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16_000:
            raise ValueError(f"Benchmark sample must be mono 16 kHz PCM WAV: {path}")
        return stream.getnframes() / stream.getframerate()


def normalize_text(text: str, language: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text)
    if language == "zh":
        return list("".join(text.split()))
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold(), re.UNICODE)


def edit_distance(left: list[str], right: list[str]) -> int:
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row, right_item in enumerate(right, 1):
        current = [row]
        for column, left_item in enumerate(left, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def compare_text(project: str, native: str, language: str) -> dict[str, Any]:
    project_units, native_units = (
        normalize_text(project, language),
        normalize_text(native, language),
    )
    distance = edit_distance(project_units, native_units)
    return {
        "metric": "cer" if language == "zh" else "wer",
        "project_units": len(project_units),
        "native_units": len(native_units),
        "project_sha256": hashlib.sha256("\0".join(project_units).encode()).hexdigest(),
        "native_sha256": hashlib.sha256("\0".join(native_units).encode()).hexdigest(),
        "edit_distance": distance,
        "difference_rate": None if not native_units else distance / len(native_units),
    }


def build_matrix(
    providers: Iterable[str] = PROVIDERS,
    languages: Iterable[str] = LANGUAGES,
    minutes: Iterable[int] = MINUTES,
    modes: Iterable[str] = MODES,
    repetitions: int = 3,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for provider in providers:
        for language in languages:
            for minute in minutes:
                for repetition in range(1, repetitions + 1):
                    order = MODES if repetition % 2 else tuple(reversed(MODES))
                    runs.extend(
                        {
                            "provider": provider,
                            "language": language,
                            "minutes": minute,
                            "mode": mode,
                            "repetition": repetition,
                        }
                        for mode in order
                        if mode in modes
                    )
    return runs


def run_id(run: dict[str, Any]) -> str:
    return "-".join(
        str(run[key])
        for key in ("provider", "language", "minutes", "mode", "repetition")
    )


def sample_gpu_mb() -> tuple[float | None, str | None]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        values = [float(line) for line in result.stdout.splitlines() if line.strip()]
        return (sum(values), None) if values else (0.0, None)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def run_worker(run: dict[str, Any], sample: Path, directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "worker.json"
    command = [
        sys.executable,
        "-m",
        "scripts.benchmark",
        "--worker",
        "--audio",
        str(sample.resolve()),
        "--provider",
        run["provider"],
        "--language",
        run["language"],
        "--mode",
        run["mode"],
        "--worker-output",
        str(output.resolve()),
        "--results-dir",
        str((directory / "results").resolve()),
    ]
    started = time.perf_counter()
    process = subprocess.Popen(command, cwd=ROOT)
    peak_rss, peak_gpu, gpu_reason = 0, None, None
    while process.poll() is None:
        try:
            root = psutil.Process(process.pid)
            peak_rss = max(
                peak_rss,
                sum(
                    item.memory_info().rss
                    for item in [root, *root.children(recursive=True)]
                ),
            )
        except (psutil.Error, OSError):
            pass
        gpu, reason = sample_gpu_mb()
        gpu_reason = gpu_reason or reason
        if gpu is not None:
            peak_gpu = max(peak_gpu or 0.0, gpu)
        time.sleep(0.2)
    duration = wav_duration(sample)
    result = (
        read_json(output)
        if output.exists()
        else {"status": "failed", "error": "worker produced no output"}
    )
    return {
        **run,
        **result,
        "run_id": run_id(run),
        "wall_seconds": time.perf_counter() - started,
        "peak_rss_bytes": peak_rss or None,
        "peak_gpu_memory_mb": peak_gpu,
        "gpu_metric_unavailable_reason": gpu_reason if peak_gpu is None else None,
        "audio_seconds": duration,
        "rtf": (time.perf_counter() - started) / duration,
        "returncode": process.returncode,
    }


def transcript_text(manifest_path: Path) -> str:
    manifest = read_json(manifest_path)
    transcript = read_json(manifest_path.parent / manifest["artifacts"]["transcript"])
    return "".join(str(segment["text"]) for segment in transcript["segments"])


def worker(
    audio: Path, provider: str, language: str, mode: str, results_dir: Path
) -> dict[str, Any]:
    from scripts.asr.chunking import ChunkLayout, decode_normalized_audio
    from scripts.asr.execution import Qwen3AsrCudaPolicy, WhisperCpuPolicy
    from scripts.asr.providers import Qwen3AsrProvider, WhisperProvider
    from scripts.runtime_options import TranscribeOptions
    from scripts.transcribe import run_transcribe

    audio = audio.resolve()
    started = time.perf_counter()
    if mode == "project-slicing":
        manifest = run_transcribe(
            audio, language=language, provider=provider, results_dir=results_dir
        )
        metrics = read_json(manifest.parent / "workspace" / "metrics.json")
        request = read_json(manifest)["request"]
        text = transcript_text(manifest)
        identity = request["execution_policy"]
        provider_identity = request["provider_identity"]
        details = {
            key: metrics.get(key)
            for key in (
                "provider_stage_seconds",
                "chunk_count",
                "batch_count",
                "hard_cut_count",
                "max_estimated_speech_duration",
                "speech_load_msre",
            )
        }
    else:
        samples = decode_normalized_audio(audio)
        if provider == "faster-whisper":
            options = TranscribeOptions(language=language)
            adapter, policy = WhisperProvider(options), WhisperCpuPolicy(options)
        else:
            adapter, policy = Qwen3AsrProvider(language), Qwen3AsrCudaPolicy()
        identity = policy.execution_identity(samples.sample_count)
        provider_identity = adapter.request_identity()
        layout = ChunkLayout(0, 0, samples.sample_count, "source", samples.sample_count)
        provider_started = time.perf_counter()
        prepared = adapter.prepare(identity)
        transcript = adapter.transcribe_one(prepared, samples.samples, layout)
        text = transcript.text
        details = {
            "provider_stage_seconds": time.perf_counter() - provider_started,
            "chunk_count": None,
            "batch_count": None,
            "hard_cut_count": None,
            "provider_native_internal_chunking": "qwen-asr 0.0.6 timestamps: up to 180 seconds"
            if provider == "qwen3-asr"
            else "single provider input with faster-whisper native VAD",
        }
    return {
        "status": "succeeded",
        "worker_seconds": time.perf_counter() - started,
        "text": text,
        "execution_identity": identity,
        "provider_identity": provider_identity,
        **details,
    }


def environment() -> dict[str, Any]:
    def command(*args: str) -> str | None:
        try:
            return subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    return {
        "platform": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "cpu_model": command("wmic", "cpu", "get", "name", "/value"),
        "gpu_model": command("nvidia-smi", "--query-gpu=name", "--format=csv,noheader"),
        "commit": command("git", "rev-parse", "HEAD"),
        "dependencies": command(sys.executable, "-m", "pip", "freeze"),
        "model_revisions": MODEL_REVISIONS,
    }


def summarize(report: dict[str, Any]) -> str:
    successful = [item for item in report["runs"] if item.get("status") == "succeeded"]
    lines = [
        "# Audio transcription benchmark",
        "",
        "Only successful runs are summarized.",
        "",
    ]
    for provider in PROVIDERS:
        rows = [item for item in successful if item["provider"] == provider]
        if not rows:
            continue
        lines += [
            f"## {provider}",
            "",
            "| Language | Minutes | Mode | Median wall | Median RTF | Provider stage | Relative speed | Difference |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for language in LANGUAGES:
            for minute in MINUTES:
                pair = [
                    item
                    for item in rows
                    if item["language"] == language and item["minutes"] == minute
                ]
                medians = {
                    mode: statistics.median(
                        item["wall_seconds"] for item in pair if item["mode"] == mode
                    )
                    for mode in MODES
                    if any(item["mode"] == mode for item in pair)
                }
                native_text = next(
                    (
                        item["text"]
                        for item in pair
                        if item["mode"] == "provider-native"
                    ),
                    None,
                )
                for mode in MODES:
                    selected = [item for item in pair if item["mode"] == mode]
                    if not selected:
                        continue
                    wall = medians[mode]
                    speed = (
                        medians.get("provider-native", 0) / wall
                        if mode == "project-slicing" and wall
                        else None
                    )
                    comparison = (
                        compare_text(selected[0]["text"], native_text, language)
                        if native_text is not None
                        else None
                    )
                    difference = comparison["difference_rate"] if comparison else None
                    lines.append(
                        f"| {language} | {minute} | {mode} | {wall:.3f}s | {statistics.median(item['rtf'] for item in selected):.4f} | {statistics.median(item['provider_stage_seconds'] for item in selected):.3f}s | {f'{speed:.3f}x' if speed is not None else '—'} | {f'{difference:.3%}' if difference is not None else '—'} |"
                    )
        lines.append("")
    return "\n".join(lines) + "\n"


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    report_path = args.report
    report = (
        read_json(report_path)
        if report_path.exists()
        else {
            "schema_version": 1,
            "environment": environment(),
            "warmups": [],
            "runs": [],
        }
    )
    prior = {item["run_id"]: item for item in report["runs"]}
    matrix = build_matrix(
        args.provider or PROVIDERS,
        args.language or LANGUAGES,
        args.minutes or MINUTES,
        args.mode or MODES,
        args.repetitions,
    )
    warmed = {
        item["provider"]
        for item in report["warmups"]
        if item.get("status") == "succeeded"
    }
    for provider in dict.fromkeys(item["provider"] for item in matrix):
        if provider not in warmed:
            warm = {
                "provider": provider,
                "language": "zh",
                "minutes": 8,
                "mode": "project-slicing",
                "repetition": 0,
            }
            report["warmups"].append(
                run_worker(
                    warm,
                    DATA_DIR / "zh-8min.wav",
                    TMP_DIR / f"warmup-{provider}-{len(report['warmups']) + 1}",
                )
            )
            write_json_atomic(report_path, report)
    for run in matrix:
        identifier = run_id(run)
        if prior.get(identifier, {}).get("status") == "succeeded":
            continue
        attempt = 1 + sum(item["run_id"] == identifier for item in report["runs"])
        result = run_worker(
            run,
            DATA_DIR / f"{run['language']}-{run['minutes']}min.wav",
            TMP_DIR / f"{identifier}-attempt-{attempt}",
        )
        result["attempt"] = attempt
        report["runs"].append(result)
        prior[identifier] = result
        counterpart = next(
            (
                item
                for item in report["runs"]
                if item.get("status") == "succeeded"
                and item["provider"] == run["provider"]
                and item["language"] == run["language"]
                and item["minutes"] == run["minutes"]
                and item["repetition"] == run["repetition"]
                and item["mode"] != run["mode"]
            ),
            None,
        )
        if result.get("status") == "succeeded" and counterpart is not None:
            project = result if result["mode"] == "project-slicing" else counterpart
            native = result if result["mode"] == "provider-native" else counterpart
            comparison = compare_text(project["text"], native["text"], run["language"])
            project["output_comparison"] = comparison
            native["output_comparison"] = comparison
        write_json_atomic(report_path, report)
        report_path.with_suffix(".md").write_text(summarize(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare project slicing with provider-native transcription."
    )
    parser.add_argument("--provider", action="append", choices=PROVIDERS)
    parser.add_argument("--language", action="append", choices=LANGUAGES)
    parser.add_argument("--minutes", action="append", type=int, choices=MINUTES)
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "benchmark" / "reports" / f"{date.today().isoformat()}.json",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--audio", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--results-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.worker:
        if (
            not args.audio
            or not args.worker_output
            or not args.results_dir
            or not args.provider
            or not args.language
            or not args.mode
        ):
            raise SystemExit("worker arguments are incomplete")
        try:
            result = worker(
                args.audio,
                args.provider[0],
                args.language[0],
                args.mode[0],
                args.results_dir,
            )
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        write_json_atomic(args.worker_output, result)
        return 0 if result["status"] == "succeeded" else 1
    run_benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
