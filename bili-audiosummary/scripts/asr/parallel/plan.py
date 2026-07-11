from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scripts.runtime_options import TranscribeOptions
from scripts.utils import path_to_posix


SCHEMA_VERSION = 3
MACRO_CHUNK_SECONDS = 1440.0
MIN_ASR_CHUNK_SECONDS = 120.0
OVERLAP_SECONDS = 5.0
MAX_WORKERS = 8
LEGAL_WORKER_COUNTS = (8, 6, 4, 2, 1)


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


def _resolve_macro_worker_config(
    macro_index: int,
    macro_duration: float,
    cpu_budget: int,
    requested_num_workers: int | None,
    requested_cpu_threads: int | None,
) -> tuple[int, int, int]:
    if requested_num_workers is not None and not 1 <= requested_num_workers <= MAX_WORKERS:
        raise ValueError(
            f"Invalid num_workers={requested_num_workers} at macro_index={macro_index}: "
            f"expected 1..{MAX_WORKERS}."
        )
    if requested_cpu_threads is not None and requested_cpu_threads < 1:
        raise ValueError(
            f"Invalid cpu_threads={requested_cpu_threads} at macro_index={macro_index}: "
            "expected >= 1."
        )

    if requested_num_workers is None:
        if requested_cpu_threads is None:
            task_workers = _select_worker_count(macro_duration, cpu_budget)
        else:
            if requested_cpu_threads > cpu_budget:
                raise ValueError(
                    f"Invalid cpu_threads={requested_cpu_threads} at macro_index={macro_index}: "
                    f"exceeds cpu_budget={cpu_budget} with minimum num_workers=1."
                )
            worker_budget = math.floor(cpu_budget / requested_cpu_threads)
            task_workers = _select_worker_count(macro_duration, worker_budget)
    else:
        task_workers = requested_num_workers

    cpu_threads = (
        requested_cpu_threads
        if requested_cpu_threads is not None
        else max(1, math.floor(cpu_budget / task_workers))
    )
    if task_workers * cpu_threads > cpu_budget:
        raise ValueError(
            f"Invalid worker configuration at macro_index={macro_index}: "
            f"num_workers={task_workers} * cpu_threads={cpu_threads} "
            f"exceeds cpu_budget={cpu_budget}."
        )

    supported_chunk_count = max(
        1,
        math.floor(macro_duration / MIN_ASR_CHUNK_SECONDS),
    )
    if requested_num_workers is not None and task_workers > supported_chunk_count:
        raise ValueError(
            f"Invalid num_workers={task_workers} at macro_index={macro_index}: "
            f"exceeds supported_chunk_count={supported_chunk_count} "
            f"for macro duration {macro_duration:.3f}s."
        )

    return task_workers, task_workers, cpu_threads


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
    requested_num_workers = _options_value(options, "num_workers", None)
    requested_cpu_threads = _options_value(options, "cpu_threads", None)
    macro_chunks: list[MacroChunkPlan] = []
    all_asr_chunks: list[AsrChunkPlan] = []

    for macro_index, macro_start, macro_duration in _macro_durations(float(duration_seconds)):
        task_workers, model_workers, cpu_threads = _resolve_macro_worker_config(
            macro_index,
            macro_duration,
            cpu_budget,
            requested_num_workers,
            requested_cpu_threads,
        )

        chunk_count = _select_chunk_count(macro_duration, task_workers)
        chunk_length = macro_duration / chunk_count
        chunks: list[AsrChunkPlan] = []
        trusted_start = macro_start
        macro_end = macro_start + macro_duration
        for chunk_index in range(chunk_count):
            if chunk_index == chunk_count - 1:
                trusted_duration = macro_end - trusted_start
            else:
                trusted_duration = chunk_length
            trusted_end = trusted_start + trusted_duration
            # 原音频的起始和结束时间，包含了重叠部分
            source_start = max(0.0, trusted_start - OVERLAP_SECONDS)
            source_end = min(float(duration_seconds), trusted_end + OVERLAP_SECONDS)
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
