from __future__ import annotations

import json
import math
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from asr_common import (
    SIMPLIFIED_CHINESE_PROMPT,
    is_chinese_language,
    make_segment,
    normalize_segments_for_language,
)
from config import DEFAULT_WHISPER_MODEL_DIR
from runtime_options import TranscribeOptions
from utils import ensure_dir, path_to_posix, read_json, resolve_ffmpeg_location, write_json


SCHEMA_VERSION = 1
MACRO_CHUNK_SECONDS = 1440.0
MIN_ASR_CHUNK_SECONDS = 120.0
OVERLAP_SECONDS = 5.0
MAX_WORKERS = 8
MAX_CHUNK_RETRIES = 1
LEGAL_WORKER_COUNTS = (8, 6, 4, 2, 1)
PROGRESS_STATES = {"pending", "running", "succeeded", "failed"}


@dataclass(frozen=True)
class AsrSourceAudio:
    path: str
    size: int
    mtime: float
    duration: float


@dataclass(frozen=True)
class AsrChunkPlan:
    macro_index: int
    chunk_index: int
    start: float
    duration: float
    source_start: float
    source_duration: float
    left_overlap: float
    right_overlap: float
    path: str


@dataclass(frozen=True)
class MacroChunkPlan:
    index: int
    start: float
    duration: float
    task_workers: int
    model_workers: int
    cpu_threads: int
    chunks: list[AsrChunkPlan]


@dataclass(frozen=True)
class ParallelAsrPlan:
    schema_version: int
    source_audio: AsrSourceAudio
    provider: str
    model: str | None
    language: str
    beam_size: int
    device: str
    compute_type: str
    cpu_budget: int
    task_workers: int
    model_workers: int
    cpu_threads: int
    macro_chunks: list[MacroChunkPlan]
    asr_chunks: list[AsrChunkPlan]
    overlap_seconds: float


def source_audio_fingerprint(audio_path: Path, duration_seconds: float) -> AsrSourceAudio:
    stat = audio_path.stat()
    return AsrSourceAudio(
        path=path_to_posix(audio_path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        duration=round(float(duration_seconds), 3),
    )


def _options_value(options: Any, name: str, default: Any = None) -> Any:
    return getattr(options, name, default)


def _round_seconds(value: float) -> float:
    return round(float(value), 3)


def _cpu_budget(cpu_count: int | None) -> int:
    if cpu_count is None:
        cpu_count = 1
    return max(1, math.floor(cpu_count * 0.75))


def _select_worker_count(unit_duration: float, cpu_budget: int) -> int:
    max_workers_by_duration = max(1, math.floor(unit_duration / MIN_ASR_CHUNK_SECONDS))
    max_allowed = min(max_workers_by_duration, cpu_budget, MAX_WORKERS)
    for worker_count in LEGAL_WORKER_COUNTS:
        if worker_count <= max_allowed:
            return worker_count
    return 1


def _select_chunk_count(unit_duration: float, task_workers: int) -> int:
    max_chunk_count = max(1, math.floor(unit_duration / MIN_ASR_CHUNK_SECONDS))
    for chunk_count in range(max_chunk_count, 0, -1):
        if chunk_count % task_workers == 0:
            if chunk_count < task_workers:
                raise ValueError("ASR chunk count cannot be smaller than task worker count.")
            return chunk_count
    raise ValueError("Unable to build a valid ASR chunk plan.")


def _macro_durations(duration_seconds: float) -> list[tuple[int, float, float]]:
    if duration_seconds <= MACRO_CHUNK_SECONDS:
        return [(0, 0.0, duration_seconds)]

    chunks: list[tuple[int, float, float]] = []
    start = 0.0
    index = 0
    while start < duration_seconds:
        macro_duration = min(MACRO_CHUNK_SECONDS, duration_seconds - start)
        chunks.append((index, start, macro_duration))
        index += 1
        start += macro_duration
    return chunks


def _chunk_path(macro_index: int, chunk_index: int) -> str:
    return path_to_posix(
        Path("chunks")
        / f"macro_{macro_index:03d}"
        / f"chunk_{chunk_index:03d}.wav"
    )


def build_parallel_asr_plan(
    duration_seconds: float,
    cpu_count: int | None,
    source_audio: AsrSourceAudio | dict[str, Any],
    options: TranscribeOptions | Any,
) -> ParallelAsrPlan:
    source = (
        source_audio
        if isinstance(source_audio, AsrSourceAudio)
        else AsrSourceAudio(
            path=str(source_audio["path"]),
            size=int(source_audio["size"]),
            mtime=float(source_audio["mtime"]),
            duration=round(float(source_audio["duration"]), 3),
        )
    )
    cpu_budget = _cpu_budget(cpu_count)
    macro_chunks: list[MacroChunkPlan] = []
    all_asr_chunks: list[AsrChunkPlan] = []

    for macro_index, macro_start, macro_duration in _macro_durations(float(duration_seconds)):
        task_workers = _select_worker_count(macro_duration, cpu_budget)
        model_workers = task_workers
        cpu_threads = max(1, math.floor(cpu_budget / task_workers))
        if task_workers * cpu_threads > cpu_budget:
            raise ValueError("Worker CPU thread budget exceeds cpu_budget.")

        chunk_count = _select_chunk_count(macro_duration, task_workers)
        chunk_length = macro_duration / chunk_count
        chunks: list[AsrChunkPlan] = []
        trusted_start = 0.0
        for chunk_index in range(chunk_count):
            if chunk_index == chunk_count - 1:
                trusted_duration = macro_duration - trusted_start
            else:
                trusted_duration = chunk_length
            trusted_end = trusted_start + trusted_duration
            source_start = max(0.0, trusted_start - OVERLAP_SECONDS)
            source_end = min(macro_duration, trusted_end + OVERLAP_SECONDS)
            left_overlap = trusted_start - source_start
            right_overlap = source_end - trusted_end
            chunk = AsrChunkPlan(
                macro_index=macro_index,
                chunk_index=chunk_index,
                start=_round_seconds(trusted_start),
                duration=_round_seconds(trusted_duration),
                source_start=_round_seconds(source_start),
                source_duration=_round_seconds(source_end - source_start),
                left_overlap=_round_seconds(left_overlap),
                right_overlap=_round_seconds(right_overlap),
                path=_chunk_path(macro_index, chunk_index),
            )
            chunks.append(chunk)
            all_asr_chunks.append(chunk)
            trusted_start = trusted_end

        macro_chunks.append(
            MacroChunkPlan(
                index=macro_index,
                start=_round_seconds(macro_start),
                duration=_round_seconds(macro_duration),
                task_workers=task_workers,
                model_workers=model_workers,
                cpu_threads=cpu_threads,
                chunks=chunks,
            )
        )

    first_macro = macro_chunks[0]
    return ParallelAsrPlan(
        schema_version=SCHEMA_VERSION,
        source_audio=source,
        provider=_options_value(options, "asr_provider", "whisper"),
        model=_options_value(options, "model", None),
        language=_options_value(options, "language", "zh"),
        beam_size=int(_options_value(options, "beam_size", 5)),
        device=_options_value(options, "device", "cpu"),
        compute_type=_options_value(options, "compute_type", "float32"),
        cpu_budget=cpu_budget,
        task_workers=first_macro.task_workers,
        model_workers=first_macro.model_workers,
        cpu_threads=first_macro.cpu_threads,
        macro_chunks=macro_chunks,
        asr_chunks=all_asr_chunks,
        overlap_seconds=OVERLAP_SECONDS,
    )


def plan_to_dict(plan: ParallelAsrPlan) -> dict[str, Any]:
    return asdict(plan)


def plan_from_dict(data: dict[str, Any]) -> ParallelAsrPlan:
    source = AsrSourceAudio(**data["source_audio"])
    macro_chunks: list[MacroChunkPlan] = []
    all_chunks: list[AsrChunkPlan] = []
    for macro_data in data["macro_chunks"]:
        chunks = [AsrChunkPlan(**chunk_data) for chunk_data in macro_data["chunks"]]
        all_chunks.extend(chunks)
        macro_chunks.append(
            MacroChunkPlan(
                index=macro_data["index"],
                start=macro_data["start"],
                duration=macro_data["duration"],
                task_workers=macro_data["task_workers"],
                model_workers=macro_data["model_workers"],
                cpu_threads=macro_data["cpu_threads"],
                chunks=chunks,
            )
        )
    return ParallelAsrPlan(
        schema_version=data["schema_version"],
        source_audio=source,
        provider=data["provider"],
        model=data.get("model"),
        language=data["language"],
        beam_size=data["beam_size"],
        device=data["device"],
        compute_type=data["compute_type"],
        cpu_budget=data["cpu_budget"],
        task_workers=data["task_workers"],
        model_workers=data["model_workers"],
        cpu_threads=data["cpu_threads"],
        macro_chunks=macro_chunks,
        asr_chunks=all_chunks,
        overlap_seconds=data["overlap_seconds"],
    )


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


def workspace_paths(workspace_dir: Path) -> dict[str, Path]:
    return {
        "root": workspace_dir,
        "plan": workspace_dir / "asr_plan.json",
        "progress": workspace_dir / "progress.json",
        "metrics": workspace_dir / "metrics.json",
        "chunks": workspace_dir / "chunks",
        "chunk_results": workspace_dir / "chunk_results",
        "merged_transcript": workspace_dir / "merged_transcript.json",
    }


def split_asr_chunks(audio_path: Path, plan: ParallelAsrPlan, workspace_dir: Path) -> None:
    ffmpeg = _ffmpeg_tool("ffmpeg")
    for macro in plan.macro_chunks:
        macro_dir = workspace_dir / "chunks" / f"macro_{macro.index:03d}"
        ensure_dir(macro_dir)
        for chunk in macro.chunks:
            chunk_path = workspace_dir / chunk.path
            ensure_dir(chunk_path.parent)
            source_start = macro.start + chunk.source_start
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{source_start:.3f}",
                "-t",
                f"{chunk.source_duration:.3f}",
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


def write_plan(path: Path, plan: ParallelAsrPlan) -> None:
    write_json(path, plan_to_dict(plan))


def load_plan(path: Path) -> ParallelAsrPlan:
    return plan_from_dict(read_json(path))


def source_matches(plan: ParallelAsrPlan, source_audio: AsrSourceAudio) -> bool:
    return plan.source_audio == source_audio


def chunk_key(chunk: AsrChunkPlan | dict[str, Any]) -> str:
    macro_index = chunk.macro_index if isinstance(chunk, AsrChunkPlan) else int(chunk["macro_index"])
    chunk_index = chunk.chunk_index if isinstance(chunk, AsrChunkPlan) else int(chunk["chunk_index"])
    return f"macro_{macro_index:03d}_chunk_{chunk_index:03d}"


def chunk_result_path(workspace_dir: Path, chunk: AsrChunkPlan) -> Path:
    return workspace_dir / "chunk_results" / f"{chunk_key(chunk)}.json"


def initial_progress(plan: ParallelAsrPlan) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "chunks": {
            chunk_key(chunk): {
                "status": "pending",
                "retry_count": 0,
                "error": None,
                "result_path": f"chunk_results/{chunk_key(chunk)}.json",
            }
            for chunk in plan.asr_chunks
        },
    }


def write_progress(path: Path, progress: dict[str, Any]) -> None:
    write_json(path, progress)


def load_progress(path: Path) -> dict[str, Any]:
    progress = read_json(path)
    for item in progress.get("chunks", {}).values():
        status = item.get("status")
        if status not in PROGRESS_STATES:
            raise ValueError(f"Invalid ASR progress status: {status}")
    return progress


def prepare_progress_for_resume(
    plan: ParallelAsrPlan,
    progress: dict[str, Any] | None,
    valid_result_keys: set[str],
) -> dict[str, Any]:
    if progress is None:
        progress = initial_progress(plan)
    chunks = progress.setdefault("chunks", {})
    for chunk in plan.asr_chunks:
        key = chunk_key(chunk)
        item = chunks.setdefault(
            key,
            {
                "status": "pending",
                "retry_count": 0,
                "error": None,
                "result_path": f"chunk_results/{key}.json",
            },
        )
        status = item.get("status", "pending")
        retry_count = int(item.get("retry_count", 0))
        if key in valid_result_keys:
            item.update({"status": "succeeded", "error": None})
        elif status == "succeeded":
            item.update({"status": "pending", "error": None})
        elif status == "running":
            item.update({"status": "pending", "error": None})
        elif status == "failed" and retry_count < MAX_CHUNK_RETRIES:
            item.update({"status": "pending", "error": None})
    return progress


def failed_chunks_blocking_merge(progress: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    for key, item in progress.get("chunks", {}).items():
        if item.get("status") == "failed" and int(item.get("retry_count", 0)) >= MAX_CHUNK_RETRIES:
            failed.append(key)
    return failed


def _valid_chunk_result(data: dict[str, Any], plan: ParallelAsrPlan) -> bool:
    required = {
        "schema_version",
        "macro_index",
        "chunk_index",
        "start",
        "duration",
        "overlap",
        "source",
        "model",
        "elapsed_seconds",
        "segments",
    }
    if not required.issubset(data):
        return False
    if data["schema_version"] != SCHEMA_VERSION:
        return False
    if data["source"] != asdict(plan.source_audio):
        return False
    return isinstance(data["segments"], list)


def load_valid_chunk_results(workspace_dir: Path, plan: ParallelAsrPlan) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    results_dir = workspace_dir / "chunk_results"
    if not results_dir.exists():
        return results
    for result_path in results_dir.glob("macro_*_chunk_*.json"):
        try:
            data = read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        if _valid_chunk_result(data, plan):
            key = chunk_key(data)
            results[key] = data
    return results


def write_chunk_result_atomic(path: Path, data: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _resolve_model_path(model: str | None) -> str:
    if model:
        return model
    if (DEFAULT_WHISPER_MODEL_DIR / "model.bin").exists():
        return path_to_posix(DEFAULT_WHISPER_MODEL_DIR)
    raise RuntimeError(
        "Local faster-whisper model is missing. Run "
        r"uv run --no-sync python scripts\setup\install_model.py --model faster-whisper "
        "before using faster-whisper ASR."
    )


def _chunk_by_key(plan: ParallelAsrPlan) -> dict[str, AsrChunkPlan]:
    return {chunk_key(chunk): chunk for chunk in plan.asr_chunks}


def _macro_by_index(plan: ParallelAsrPlan) -> dict[int, MacroChunkPlan]:
    return {macro.index: macro for macro in plan.macro_chunks}


def _transcribe_chunk(
    model: Any,
    chunk_path: Path,
    plan: ParallelAsrPlan,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    segments, info = model.transcribe(
        path_to_posix(chunk_path),
        language=plan.language,
        beam_size=plan.beam_size,
        vad_filter=True,
        initial_prompt=SIMPLIFIED_CHINESE_PROMPT if is_chinese_language(plan.language) else None,
    )
    segment_list = normalize_segments_for_language(
        [make_segment(segment) for segment in segments],
        plan.language,
    )
    info_data = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "duration_after_vad": getattr(info, "duration_after_vad", None),
    }
    return info_data, segment_list


def _chunk_result_payload(
    plan: ParallelAsrPlan,
    macro: MacroChunkPlan,
    chunk: AsrChunkPlan,
    chunk_path: Path,
    model_path: str,
    elapsed_seconds: float,
    info: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "macro_index": chunk.macro_index,
        "chunk_index": chunk.chunk_index,
        "start": chunk.start,
        "duration": chunk.duration,
        "source_start": chunk.source_start,
        "source_duration": chunk.source_duration,
        "overlap": {
            "left": chunk.left_overlap,
            "right": chunk.right_overlap,
        },
        "source": asdict(plan.source_audio),
        "chunk_audio_path": path_to_posix(chunk_path),
        "model": {
            "path": model_path,
            "language": plan.language,
            "beam_size": plan.beam_size,
            "device": plan.device,
            "compute_type": plan.compute_type,
            "cpu_threads": macro.cpu_threads,
            "model_workers": macro.model_workers,
        },
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "info": info,
        "segments": segments,
    }


def _submit_macro_chunks(
    model: Any,
    plan: ParallelAsrPlan,
    macro: MacroChunkPlan,
    workspace_dir: Path,
    progress_path: Path,
    progress: dict[str, Any],
    chunk_results: dict[str, dict[str, Any]],
    model_path: str,
) -> None:
    pending = [
        chunk
        for chunk in macro.chunks
        if progress["chunks"][chunk_key(chunk)]["status"] == "pending"
    ]
    if not pending:
        return

    def run_one(chunk: AsrChunkPlan) -> tuple[AsrChunkPlan, float, dict[str, Any], list[dict[str, Any]]]:
        started_at = time.perf_counter()
        info, segments = _transcribe_chunk(
            model,
            workspace_dir / chunk.path,
            plan,
        )
        return chunk, time.perf_counter() - started_at, info, segments

    max_workers = macro.task_workers if macro.task_workers > 1 else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for chunk in pending:
            key = chunk_key(chunk)
            progress["chunks"][key]["status"] = "running"
            write_progress(progress_path, progress)
            futures[executor.submit(run_one, chunk)] = chunk

        for future in as_completed(futures):
            chunk = futures[future]
            key = chunk_key(chunk)
            try:
                finished_chunk, elapsed, info, segments = future.result()
            except Exception as exc:
                item = progress["chunks"][key]
                retry_count = int(item.get("retry_count", 0))
                item.update(
                    {
                        "status": "failed",
                        "retry_count": retry_count,
                        "error": str(exc),
                    }
                )
                write_progress(progress_path, progress)
                if retry_count < MAX_CHUNK_RETRIES:
                    item.update(
                        {
                            "status": "pending",
                            "retry_count": retry_count + 1,
                            "error": None,
                        }
                    )
                    write_progress(progress_path, progress)
                continue

            result = _chunk_result_payload(
                plan,
                macro,
                finished_chunk,
                workspace_dir / finished_chunk.path,
                model_path,
                elapsed,
                info,
                segments,
            )
            result_path = chunk_result_path(workspace_dir, finished_chunk)
            write_chunk_result_atomic(result_path, result)
            chunk_results[key] = result
            progress["chunks"][key].update(
                {
                    "status": "succeeded",
                    "error": None,
                }
            )
            write_progress(progress_path, progress)


def transcribe_whisper_chunks(
    plan: ParallelAsrPlan,
    options: TranscribeOptions,
    workspace_dir: Path,
) -> dict[str, dict[str, Any]]:
    paths = workspace_paths(workspace_dir)
    progress_path = paths["progress"]
    valid_results = load_valid_chunk_results(workspace_dir, plan)
    progress = load_progress(progress_path) if progress_path.exists() else None
    progress = prepare_progress_for_resume(plan, progress, set(valid_results))
    write_progress(progress_path, progress)

    blocking = failed_chunks_blocking_merge(progress)
    if blocking:
        raise RuntimeError(f"ASR chunk failed after retry: {', '.join(blocking)}")

    pending_exists = any(
        progress["chunks"][chunk_key(chunk)]["status"] == "pending"
        for chunk in plan.asr_chunks
    )
    if not pending_exists:
        return valid_results

    from faster_whisper import WhisperModel

    model_path = _resolve_model_path(options.model)
    model = WhisperModel(
        model_path,
        device=plan.device,
        compute_type=plan.compute_type,
        cpu_threads=plan.cpu_threads,
        num_workers=plan.model_workers,
    )

    chunk_results = dict(valid_results)
    for macro in plan.macro_chunks:
        while True:
            _submit_macro_chunks(
                model,
                plan,
                macro,
                workspace_dir,
                progress_path,
                progress,
                chunk_results,
                model_path,
            )
            blocking = failed_chunks_blocking_merge(progress)
            if blocking:
                raise RuntimeError(f"ASR chunk failed after retry: {', '.join(blocking)}")
            pending = [
                chunk
                for chunk in macro.chunks
                if progress["chunks"][chunk_key(chunk)]["status"] == "pending"
            ]
            if not pending:
                break
    return chunk_results


def merge_chunk_results(
    plan: ParallelAsrPlan,
    chunk_results: dict[str, dict[str, Any]] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result_map = (
        {chunk_key(result): result for result in chunk_results}
        if isinstance(chunk_results, list)
        else chunk_results
    )
    macros = _macro_by_index(plan)
    chunks = _chunk_by_key(plan)
    merged: list[dict[str, Any]] = []
    previous_start = 0.0

    for key in sorted(chunks, key=lambda value: (chunks[value].macro_index, chunks[value].chunk_index)):
        if key not in result_map:
            raise RuntimeError(f"Missing ASR chunk result: {key}")
        chunk = chunks[key]
        macro = macros[chunk.macro_index]
        result = result_map[key]
        trusted_start = macro.start + chunk.start
        trusted_end = trusted_start + chunk.duration
        offset = macro.start + chunk.source_start
        for segment in result.get("segments", []):
            global_start = round(float(segment["start"]) + offset, 3)
            global_end = round(float(segment["end"]) + offset, 3)
            midpoint = (global_start + global_end) / 2
            if midpoint < trusted_start or midpoint > trusted_end:
                continue
            merged.append(
                {
                    **segment,
                    "id": 0,
                    "start": global_start,
                    "end": global_end,
                    "_macro_index": chunk.macro_index,
                    "_chunk_index": chunk.chunk_index,
                }
            )

    merged.sort(
        key=lambda segment: (
            int(segment["_macro_index"]),
            int(segment["_chunk_index"]),
            float(segment["start"]),
            float(segment["end"]),
        )
    )
    for index, segment in enumerate(merged):
        start = float(segment["start"])
        if index > 0 and start < previous_start:
            raise RuntimeError("Merged ASR timestamps are not monotonic.")
        if float(segment["end"]) < start:
            raise RuntimeError("Merged ASR segment end is earlier than start.")
        previous_start = start
        segment["id"] = index
        del segment["_macro_index"]
        del segment["_chunk_index"]
    return merged


def write_metrics(
    path: Path,
    plan: ParallelAsrPlan,
    total_elapsed_seconds: float,
    chunk_results: dict[str, dict[str, Any]],
    failed_chunks: list[str] | None = None,
    macro_elapsed_seconds: list[dict[str, Any]] | None = None,
    segment_count: int | None = None,
) -> dict[str, Any]:
    sorted_results = [
        chunk_results[key]
        for key in sorted(chunk_results, key=lambda value: (chunk_results[value]["macro_index"], chunk_results[value]["chunk_index"]))
    ]
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "total_elapsed_seconds": round(float(total_elapsed_seconds), 3),
        "macro_elapsed_seconds": macro_elapsed_seconds or [],
        "chunk_elapsed_seconds": [
            {
                "macro_index": result["macro_index"],
                "chunk_index": result["chunk_index"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
            for result in sorted_results
        ],
        "task_workers": plan.task_workers,
        "model_workers": plan.model_workers,
        "cpu_threads": plan.cpu_threads,
        "chunk_count": len(plan.asr_chunks),
        "segment_count": segment_count
        if segment_count is not None
        else sum(len(result.get("segments", [])) for result in sorted_results),
        "failed_chunks": failed_chunks or [],
    }
    write_json(path, metrics)
    return metrics


def build_macro_elapsed_from_results(
    plan: ParallelAsrPlan,
    chunk_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    macro_elapsed: list[dict[str, Any]] = []
    for macro in plan.macro_chunks:
        elapsed_values = [
            float(chunk_results[chunk_key(chunk)]["elapsed_seconds"])
            for chunk in macro.chunks
            if chunk_key(chunk) in chunk_results
        ]
        macro_elapsed.append(
            {
                "macro_index": macro.index,
                "elapsed_seconds": round(max(elapsed_values), 3)
                if elapsed_values
                else 0.0,
                "chunk_count": len(macro.chunks),
            }
        )
    return macro_elapsed


def _load_or_create_plan(
    plan_path: Path,
    current_plan: ParallelAsrPlan,
) -> tuple[ParallelAsrPlan, bool]:
    if plan_path.exists():
        existing_plan = load_plan(plan_path)
        if source_matches(existing_plan, current_plan.source_audio):
            return existing_plan, False
    write_plan(plan_path, current_plan)
    return current_plan, True


def run_parallel_whisper_transcribe(
    audio_path: Path,
    options: TranscribeOptions,
    output_dir: Path,
    duration_seconds: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    started_at = time.perf_counter()
    duration = duration_seconds if duration_seconds is not None else probe_audio_duration(audio_path)
    source_audio = source_audio_fingerprint(audio_path, duration)
    model_path = _resolve_model_path(options.model)
    plan_options = TranscribeOptions(
        **{
            **options.__dict__,
            "model": model_path,
        }
    )
    current_plan = build_parallel_asr_plan(
        duration_seconds=duration,
        cpu_count=os.cpu_count(),
        source_audio=source_audio,
        options=plan_options,
    )
    workspace_dir = output_dir / "asr_parallel"
    paths = workspace_paths(workspace_dir)
    ensure_dir(paths["root"])
    plan, rebuilt_plan = _load_or_create_plan(paths["plan"], current_plan)
    if rebuilt_plan or not paths["progress"].exists():
        write_progress(paths["progress"], initial_progress(plan))

    split_asr_chunks(audio_path, plan, workspace_dir)
    chunk_results = transcribe_whisper_chunks(plan, plan_options, workspace_dir)
    merged_segments = merge_chunk_results(plan, chunk_results)
    write_json(paths["merged_transcript"], {"segments": merged_segments})
    progress = load_progress(paths["progress"])
    failed_chunks = failed_chunks_blocking_merge(progress)
    write_metrics(
        paths["metrics"],
        plan,
        time.perf_counter() - started_at,
        chunk_results,
        failed_chunks,
        build_macro_elapsed_from_results(plan, chunk_results),
        len(merged_segments),
    )
    info_data = {
        "language": plan.language,
        "language_probability": None,
        "duration": duration,
        "duration_after_vad": None,
        "model": model_path,
        "device": plan.device,
        "compute_type": plan.compute_type,
        "beam_size": plan.beam_size,
        "text_normalization": "simplified-chinese" if is_chinese_language(plan.language) else None,
    }
    return info_data, merged_segments, "faster-whisper"
