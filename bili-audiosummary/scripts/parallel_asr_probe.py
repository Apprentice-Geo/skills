from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.asr.common import (
    SIMPLIFIED_CHINESE_PROMPT,
    is_chinese_language,
    make_segment,
    normalize_segments_for_language,
)
from scripts.asr.qwen3 import (
    has_model_weights,
    transcribe_with_qwen3,
)
from scripts.config import (
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_DEVICE,
    DEFAULT_TRANSCRIBE_BEAM_SIZE,
    DEFAULT_TRANSCRIBE_LANGUAGE,
    DEFAULT_WHISPER_MODEL_DIR,
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ASR_MODEL_DIR,
)
from scripts.runtime_options import TranscribeOptions
from scripts.transcribe import transcribe_audio
from scripts.utils import ensure_dir, path_to_posix, write_json


@dataclass(frozen=True)
class Chunk:
    index: int
    path: Path
    start: float
    duration: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe chunk-level parallel ASR behavior without changing the production pipeline."
    )
    parser.add_argument("audio", type=Path)
    parser.add_argument(
        "--provider",
        choices=("whisper", "qwen3"),
        required=True,
        help="Provider to test. Qwen3 is checked directly and does not fall back to faster-whisper.",
    )
    parser.add_argument("--language", default=DEFAULT_TRANSCRIBE_LANGUAGE)
    parser.add_argument("--chunk-seconds", type=float, default=180.0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--model-workers",
        type=int,
        help="For faster-whisper thread mode: WhisperModel num_workers. Defaults to --workers.",
    )
    parser.add_argument("--cpu-threads", type=int, default=0)
    parser.add_argument(
        "--whisper-mode",
        choices=("process", "thread"),
        default="process",
        help="For faster-whisper only: process loads one model per process; thread shares one model with num_workers.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("test_workspace") / "parallel_asr_probe",
    )
    return parser.parse_args()


def run_command(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def require_tool(name: str) -> str:
    tool = shutil.which(name)
    if not tool:
        raise RuntimeError(f"{name} is required for this probe.")
    return tool


def probe_duration(audio_path: Path) -> float:
    ffprobe = require_tool("ffprobe")
    output = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(audio_path),
        ]
    )
    return float(json.loads(output)["format"]["duration"])


def split_audio(audio_path: Path, output_dir: Path, chunk_seconds: float) -> list[Chunk]:
    ffmpeg = require_tool("ffmpeg")
    duration = probe_duration(audio_path)
    chunks_dir = output_dir / "chunks"
    ensure_dir(chunks_dir)
    chunks: list[Chunk] = []

    index = 0
    start = 0.0
    while start < duration:
        chunk_duration = min(chunk_seconds, duration - start)
        chunk_path = chunks_dir / f"{audio_path.stem}_{index:03d}.wav"
        run_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{chunk_duration:.3f}",
                "-i",
                str(audio_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(chunk_path),
            ]
        )
        chunks.append(Chunk(index=index, path=chunk_path, start=start, duration=chunk_duration))
        index += 1
        start += chunk_seconds

    return chunks


def check_qwen3_available() -> None:
    try:
        import torch
        import qwen_asr  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Qwen3 optional dependencies are not installed in this venv.") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("Qwen3 requires CUDA, but torch.cuda.is_available() is false.")
    if not has_model_weights(QWEN3_ASR_MODEL_DIR) or not has_model_weights(QWEN3_ALIGNER_MODEL_DIR):
        raise RuntimeError("Qwen3 model weights are missing.")


def transcribe_chunk(provider: str, chunk: Chunk, language: str) -> dict[str, Any]:
    started_at = time.perf_counter()
    if provider == "whisper":
        options = TranscribeOptions(
            audio=chunk.path,
            asr_provider="whisper",
            model=path_to_posix(DEFAULT_WHISPER_MODEL_DIR),
            language=language,
            device=DEFAULT_TRANSCRIBE_DEVICE,
            compute_type=DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
            num_workers=1,
        )
        info, segments, source = transcribe_audio(
            chunk.path,
            options,
            {"duration": chunk.duration},
        )
    else:
        info, segments = transcribe_with_qwen3(chunk.path, language, chunk.duration)
        source = "qwen3-asr"

    return {
        "index": chunk.index,
        "path": path_to_posix(chunk.path),
        "start": chunk.start,
        "duration": chunk.duration,
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        "source": source,
        "info": info,
        "segments": segments,
    }


def transcribe_whisper_chunk_with_model(model: Any, chunk: Chunk, language: str) -> dict[str, Any]:
    started_at = time.perf_counter()
    segments, info = model.transcribe(
        path_to_posix(chunk.path),
        language=language,
        beam_size=DEFAULT_TRANSCRIBE_BEAM_SIZE,
        vad_filter=True,
        initial_prompt=SIMPLIFIED_CHINESE_PROMPT if is_chinese_language(language) else None,
    )
    segment_list = normalize_segments_for_language(
        [make_segment(segment) for segment in segments],
        language,
    )
    return {
        "index": chunk.index,
        "path": path_to_posix(chunk.path),
        "start": chunk.start,
        "duration": chunk.duration,
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        "source": "faster-whisper",
        "info": {
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "duration_after_vad": getattr(info, "duration_after_vad", None),
            "model": path_to_posix(DEFAULT_WHISPER_MODEL_DIR),
            "device": DEFAULT_TRANSCRIBE_DEVICE,
            "compute_type": DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
            "beam_size": DEFAULT_TRANSCRIBE_BEAM_SIZE,
            "mode": "thread-shared-model",
        },
        "segments": segment_list,
    }


def transcribe_whisper_chunks_with_threads(
    chunks: list[Chunk],
    language: str,
    workers: int,
    model_workers: int,
    cpu_threads: int,
) -> list[dict[str, Any]]:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        path_to_posix(DEFAULT_WHISPER_MODEL_DIR),
        device=DEFAULT_TRANSCRIBE_DEVICE,
        compute_type=DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
        cpu_threads=cpu_threads,
        num_workers=model_workers,
    )
    chunk_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(transcribe_whisper_chunk_with_model, model, chunk, language)
            for chunk in chunks
        ]
        for future in as_completed(futures):
            chunk_results.append(future.result())
    return chunk_results


def transcribe_chunks_with_processes(
    provider: str,
    chunks: list[Chunk],
    language: str,
    workers: int,
) -> list[dict[str, Any]]:
    chunk_results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(transcribe_chunk, provider, chunk, language)
            for chunk in chunks
        ]
        for future in as_completed(futures):
            chunk_results.append(future.result())
    return chunk_results


def merge_chunk_results(chunk_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for chunk_result in sorted(chunk_results, key=lambda item: item["index"]):
        offset = float(chunk_result["start"])
        for segment in chunk_result["segments"]:
            merged.append(
                {
                    **segment,
                    "id": len(merged),
                    "start": round(float(segment["start"]) + offset, 3),
                    "end": round(float(segment["end"]) + offset, 3),
                    "chunk_index": chunk_result["index"],
                }
            )
    return merged


def main() -> int:
    args = parse_args()
    audio_path = args.audio.resolve()
    run_name = args.provider
    if args.provider == "whisper":
        run_name = f"{args.provider}_{args.whisper_mode}"
    output_dir = args.output_dir.resolve() / run_name
    ensure_dir(output_dir)

    if args.provider == "qwen3":
        check_qwen3_available()

    started_at = time.perf_counter()
    audio_duration = probe_duration(audio_path)
    chunks = split_audio(audio_path, output_dir, args.chunk_seconds)
    model_workers = args.model_workers if args.model_workers is not None else args.workers

    if args.provider == "whisper" and args.whisper_mode == "thread":
        chunk_results = transcribe_whisper_chunks_with_threads(
            chunks,
            args.language,
            args.workers,
            model_workers,
            args.cpu_threads,
        )
    else:
        chunk_results = transcribe_chunks_with_processes(
            args.provider,
            chunks,
            args.language,
            args.workers,
        )

    merged_segments = merge_chunk_results(chunk_results)
    chunk_elapsed_seconds = [
        item["elapsed_seconds"] for item in sorted(chunk_results, key=lambda item: item["index"])
    ]
    summary = {
        "task_workers": args.workers,
        "model_workers": model_workers if args.provider == "whisper" and args.whisper_mode == "thread" else None,
        "cpu_threads": args.cpu_threads,
        "thread_budget": model_workers * args.cpu_threads if args.cpu_threads else None,
        "chunk_count": len(chunks),
        "chunk_elapsed_seconds": chunk_elapsed_seconds,
        "max_chunk_elapsed_seconds": max(chunk_elapsed_seconds) if chunk_elapsed_seconds else 0,
        "segment_count": len(merged_segments),
    }
    payload = {
        "audio_path": path_to_posix(audio_path),
        "provider": args.provider,
        "whisper_mode": args.whisper_mode if args.provider == "whisper" else None,
        "language": args.language,
        "audio_duration": round(audio_duration, 3),
        "chunk_seconds": args.chunk_seconds,
        "workers": args.workers,
        "model_workers": model_workers if args.provider == "whisper" and args.whisper_mode == "thread" else None,
        "cpu_threads": args.cpu_threads,
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        "summary": summary,
        "chunks": chunk_results,
        "segments": merged_segments,
    }

    chunk_label = str(args.chunk_seconds).replace(".", "p")
    model_workers_label = f"_mw{model_workers}" if args.provider == "whisper" and args.whisper_mode == "thread" else ""
    output_path = output_dir / (
        f"{audio_path.stem}_{run_name}_w{args.workers}{model_workers_label}_t{args.cpu_threads}_c{chunk_label}_parallel_probe.json"
    )
    write_json(output_path, payload)
    print(f"Wrote {path_to_posix(output_path)}")
    print(
        " ".join(
            [
                f"workers={args.workers}",
                f"model_workers={model_workers}",
                f"cpu_threads={args.cpu_threads}",
                f"chunks={summary['chunk_count']}",
                f"segments={summary['segment_count']}",
                f"max_chunk_elapsed={summary['max_chunk_elapsed_seconds']}s",
                f"elapsed={payload['elapsed_seconds']}s",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
