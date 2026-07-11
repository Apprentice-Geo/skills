from __future__ import annotations

import os
import time
from pathlib import Path

from scripts.asr.common import is_chinese_language
from scripts.asr.parallel.media import probe_audio_duration, split_asr_chunks
from scripts.asr.parallel.merge import merge_chunk_results
from scripts.asr.parallel.metrics import build_macro_elapsed_from_results, write_metrics
from scripts.asr.parallel.plan import ParallelAsrPlan, build_parallel_asr_plan, source_audio_fingerprint
from scripts.asr.parallel.state import (
    failed_chunks_blocking_merge,
    initial_progress,
    load_plan,
    load_progress,
    workspace_paths,
    write_plan,
    write_progress,
)
from scripts.asr.parallel.worker import _resolve_model_path, transcribe_whisper_chunks
from scripts.process_logging import get_logger, terminal_info
from scripts.runtime_options import TranscribeOptions
from scripts.utils import ensure_dir, write_json


logger = get_logger(__name__)


def _load_or_create_plan(
    plan_path: Path,
    current_plan: ParallelAsrPlan,
) -> tuple[ParallelAsrPlan, str]:
    if plan_path.exists():
        try:
            existing_plan = load_plan(plan_path)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Invalid cached ASR plan; rebuilding: %s", exc)
            write_plan(plan_path, current_plan)
            return current_plan, "rebuilt"
        if existing_plan == current_plan:
            return existing_plan, "reused"
        write_plan(plan_path, current_plan)
        return current_plan, "rebuilt"
    write_plan(plan_path, current_plan)
    return current_plan, "created"


def _log_plan(plan: ParallelAsrPlan, status: str) -> None:
    terminal_info(
        logger,
        "[Transcribe] plan: status=%s, macros=%d, chunks=%d, cpu_budget=%d, overlap=%.3fs",
        status,
        len(plan.macro_chunks),
        len(plan.asr_chunks),
        plan.cpu_budget,
        plan.overlap_seconds,
    )
    for macro in plan.macro_chunks:
        terminal_info(
            logger,
            "[Transcribe] macro_%03d plan: chunks=%d, task_workers=%d, model_workers=%d, cpu_threads=%d",
            macro.index,
            len(macro.chunks),
            macro.task_workers,
            macro.model_workers,
            macro.cpu_threads,
        )


def run_parallel_whisper_transcribe(
    audio_path: Path,
    options: TranscribeOptions,
    output_dir: Path,
    duration_seconds: float | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], str]:
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
    plan, plan_status = _load_or_create_plan(paths["plan"], current_plan)
    if plan_status == "rebuilt":
        terminal_info(logger, "[Transcribe] cached plan incompatible; rebuilding")
    _log_plan(plan, plan_status)
    if plan_status != "reused" or not paths["progress"].exists():
        write_progress(paths["progress"], initial_progress(plan))

    terminal_info(
        logger,
        "[Transcribe] preparing %d audio chunks",
        len(plan.asr_chunks),
    )
    split_asr_chunks(audio_path, plan, workspace_dir)
    terminal_info(logger, "[Transcribe] audio chunks ready")
    chunk_results = transcribe_whisper_chunks(plan, plan_options, workspace_dir)
    merged_segments = merge_chunk_results(plan, chunk_results)
    terminal_info(
        logger,
        "[Transcribe] merge succeeded: chunks=%d, segments=%d",
        len(chunk_results),
        len(merged_segments),
    )
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
