from __future__ import annotations

import os
import time
from pathlib import Path

from scripts.asr.common import is_chinese_language
from scripts.asr.parallel.media import (
    detect_speech_intervals,
    probe_audio_duration,
    split_asr_chunks,
)
from scripts.asr.parallel.merge import merge_chunk_results
from scripts.asr.parallel.metrics import write_metrics
from scripts.asr.parallel.plan import (
    DEFAULT_VAD_PARAMETERS,
    ParallelAsrPlan,
    build_parallel_asr_plan,
    plan_matches_request,
    resolve_worker_config,
    source_audio_fingerprint,
)
from scripts.asr.parallel.state import (
    chunk_key,
    failed_chunks_blocking_merge,
    initial_progress,
    load_plan,
    load_progress,
    load_valid_chunk_results,
    workspace_paths,
    write_plan,
    write_progress,
)
from scripts.asr.parallel.worker import _resolve_model_path, transcribe_whisper_chunks
from scripts.process_logging import get_logger, terminal_info
from scripts.runtime_options import TranscribeOptions
from scripts.utils import ensure_dir, write_json


logger = get_logger(__name__)


def _log_plan(plan: ParallelAsrPlan, status: str) -> None:
    terminal_info(
        logger,
        "[Transcribe] plan: status=%s, chunks=%d, num_workers=%d, "
        "cpu_threads=%d, cpu_budget=%d, batches=%d",
        status,
        len(plan.chunks),
        plan.num_workers,
        plan.cpu_threads,
        plan.cpu_budget,
        len(plan.chunks) // plan.num_workers,
    )


def _load_matching_plan(
    plan_path: Path,
    source_audio,
    options: TranscribeOptions,
    worker_config,
) -> ParallelAsrPlan | None:
    if not plan_path.exists():
        return None
    try:
        plan = load_plan(plan_path)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Invalid cached ASR plan; rebuilding: %s", exc)
        return None
    if plan_matches_request(
        plan,
        source_audio,
        options,
        worker_config,
        DEFAULT_VAD_PARAMETERS,
    ):
        return plan
    return None


def run_parallel_whisper_transcribe(
    audio_path: Path,
    options: TranscribeOptions,
    output_dir: Path,
    duration_seconds: float | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], str]:
    started_at = time.perf_counter()
    duration = (
        duration_seconds
        if duration_seconds is not None
        else probe_audio_duration(audio_path)
    )
    source_audio = source_audio_fingerprint(audio_path, duration)
    cpu_count = os.cpu_count()
    worker_config = resolve_worker_config(duration, cpu_count, options)
    model_path = _resolve_model_path(options.model)
    plan_options = TranscribeOptions(
        **{
            **options.__dict__,
            "model": model_path,
        }
    )
    workspace_dir = output_dir / "asr_parallel"
    paths = workspace_paths(workspace_dir)

    plan_existed = paths["plan"].exists()
    plan = _load_matching_plan(
        paths["plan"],
        source_audio,
        plan_options,
        worker_config,
    )
    if plan is None:
        speech_intervals = detect_speech_intervals(
            audio_path,
            DEFAULT_VAD_PARAMETERS,
        )
        plan = build_parallel_asr_plan(
            duration_seconds=duration,
            cpu_count=cpu_count,
            source_audio=source_audio,
            options=plan_options,
            speech_intervals=speech_intervals,
            vad_parameters=DEFAULT_VAD_PARAMETERS,
            worker_config=worker_config,
        )
        ensure_dir(paths["root"])
        write_plan(paths["plan"], plan)
        plan_status = "rebuilt" if plan_existed else "created"
    else:
        plan_status = "reused"

    if plan_status == "rebuilt":
        terminal_info(logger, "[Transcribe] cached plan incompatible; rebuilding")
    _log_plan(plan, plan_status)
    if plan_status != "reused" or not paths["progress"].exists():
        write_progress(paths["progress"], initial_progress(plan))

    valid_results = load_valid_chunk_results(workspace_dir, plan)
    pending_chunks = [
        chunk for chunk in plan.chunks if chunk_key(chunk) not in valid_results
    ]
    terminal_info(
        logger,
        "[Transcribe] preparing %d audio chunks",
        len(pending_chunks),
    )
    if pending_chunks:
        split_asr_chunks(
            audio_path,
            plan,
            workspace_dir,
            chunks=pending_chunks,
        )
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
        len(merged_segments),
        failed_chunks,
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
        "text_normalization": (
            "simplified-chinese" if is_chinese_language(plan.language) else None
        ),
    }
    return info_data, merged_segments, "faster-whisper"
