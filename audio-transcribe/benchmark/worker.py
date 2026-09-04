from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import psutil

from benchmark import LANGUAGES, MODES, PROVIDERS, ROOT, TMP_DIR
from benchmark.report import run_id
from scripts.io_utils import read_json, write_json_atomic


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16_000:
            raise ValueError(f"Benchmark sample must be mono 16 kHz PCM WAV: {path}")
        return stream.getnframes() / stream.getframerate()


def fresh_worker_directory(label: str) -> Path:
    return TMP_DIR / f"{label}-{uuid.uuid4().hex}"


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
        "benchmark.worker",
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
    wall_seconds = time.perf_counter() - started
    return {
        **run,
        **result,
        "run_id": run_id(run),
        "wall_seconds": wall_seconds,
        "peak_rss_bytes": peak_rss or None,
        "peak_gpu_memory_mb": peak_gpu,
        "gpu_metric_unavailable_reason": gpu_reason if peak_gpu is None else None,
        "audio_seconds": duration,
        "rtf": wall_seconds / duration,
        "returncode": process.returncode,
    }


def transcript_text(manifest_path: Path) -> str:
    manifest = read_json(manifest_path)
    transcript = read_json(manifest_path.parent / manifest["artifacts"]["transcript"])
    return "".join(str(segment["text"]) for segment in transcript["segments"])


def native_whisper_configuration(
    sample_count: int, language: str
) -> tuple[Any, Any, dict[str, Any]]:
    """Give one native Whisper inference the same CPU budget as slicing runs."""
    from scripts.asr.execution import WhisperCpuPolicy
    from scripts.asr.providers import WhisperProvider
    from scripts.runtime_options import TranscribeOptions

    default_options = TranscribeOptions(language=language)
    budget = WhisperCpuPolicy(default_options).execution_identity(sample_count)[
        "cpu_budget"
    ]
    options = TranscribeOptions(language=language, cpu_threads=int(budget))
    policy = WhisperCpuPolicy(options)
    return WhisperProvider(options), policy, policy.execution_identity(sample_count)


def worker(
    audio: Path, provider: str, language: str, mode: str, results_dir: Path
) -> dict[str, Any]:
    from scripts.asr.chunking import ChunkLayout, decode_normalized_audio
    from scripts.asr.execution import Qwen3AsrCudaPolicy
    from scripts.asr.providers import Qwen3AsrProvider
    from scripts.transcribe import run_transcribe

    audio = audio.resolve()
    started = time.perf_counter()
    if mode == "project-slicing":
        outcome = run_transcribe(
            audio, language=language, provider=provider, results_dir=results_dir
        )
        manifest = outcome.manifest_path
        if outcome.pipeline_outcome is None:
            raise RuntimeError(
                "Fresh project-slicing benchmark run produced no pipeline diagnostics"
            )
        metrics = outcome.pipeline_outcome.metrics
        request = read_json(manifest)["request"]
        text = transcript_text(manifest)
        identity = request["execution_policy"]
        provider_identity = request["provider_identity"]
        details = {
            key: getattr(metrics, key)
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
            adapter, policy, identity = native_whisper_configuration(
                samples.sample_count, language
            )
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one benchmark transcription worker."
    )
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--language", choices=LANGUAGES, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--worker-output", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = worker(
            args.audio,
            args.provider,
            args.language,
            args.mode,
            args.results_dir,
        )
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    write_json_atomic(args.worker_output, result)
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
