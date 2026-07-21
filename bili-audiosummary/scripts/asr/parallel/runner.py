from __future__ import annotations

import os
import time
from pathlib import Path

from scripts.asr.chunking import decode_normalized_audio
from scripts.asr.common import is_chinese_language
from scripts.asr.parallel.media import detect_speech_intervals
from scripts.asr.parallel.merge import merge_chunk_results
from scripts.asr.parallel.metrics import write_metrics
from scripts.asr.parallel.plan import (
    DEFAULT_VAD_PARAMETERS,
    ParallelAsrPlan,
    build_parallel_asr_plan,
    plan_matches_request,
    resolve_worker_config,
    source_audio_fingerprint,
    source_file_matches,
)
from scripts.asr.parallel.state import (
    failed_chunks_blocking_merge,
    initial_progress,
    load_plan,
    load_progress,
    load_valid_chunk_results,
    load_valid_vad_result,
    workspace_paths,
    write_plan,
    write_progress,
    write_vad_result,
)
from scripts.asr.parallel.worker import _resolve_model_path, transcribe_whisper_chunks
from scripts.process_logging import get_logger, terminal_info
from scripts.runtime_options import TranscribeOptions
from scripts.utils import write_json_atomic

logger = get_logger(__name__)


def _log_plan(plan: ParallelAsrPlan, status: str) -> None:
    terminal_info(
        logger,
        "[Transcribe] plan: status=%s, chunks=%d, num_workers=%d, cpu_threads=%d, cpu_budget=%d, batches=%d",
        status,
        len(plan.chunks),
        plan.num_workers,
        plan.cpu_threads,
        plan.cpu_budget,
        plan.batch_count,
    )


def _options_with_model(options: TranscribeOptions, model: str) -> TranscribeOptions:
    return TranscribeOptions(**{**options.__dict__, "model": model})


def _load_matching_plan(
    plan_path: Path,
    audio_path: Path,
    options: TranscribeOptions,
) -> tuple[ParallelAsrPlan, TranscribeOptions] | None:
    if not plan_path.exists():
        return None
    try:
        plan = load_plan(plan_path)
        if not source_file_matches(plan.source_audio, audio_path):
            return None
        if options.model is not None and options.model != plan.model:
            return None
        plan_options = _options_with_model(options, str(plan.model))
        workers = resolve_worker_config(
            plan.source_audio.sample_count, os.cpu_count(), plan_options
        )
        if not plan_matches_request(plan, plan.source_audio, plan_options, workers):
            return None
        return plan, plan_options
    except (OSError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Invalid cached ASR plan; rebuilding: %s", exc)
        return None


def run_parallel_whisper_transcribe(
    audio_path: Path,
    options: TranscribeOptions,
    workspace_dir: Path,
) -> tuple[dict[str, object], list[dict[str, object]], str]:
    started_at = time.perf_counter()
    paths = workspace_paths(workspace_dir)
    matched = _load_matching_plan(paths["plan"], audio_path, options)
    plan = matched[0] if matched else None
    plan_options = matched[1] if matched else None

    if plan is not None:
        cached = load_valid_chunk_results(workspace_dir, plan)
        if len(cached) == len(plan.chunks):
            terminal_info(
                logger,
                "[Transcribe] cache: complete; skipped audio decode and model load",
            )
            merged = merge_chunk_results(plan, cached)
            if not paths["merged_transcript"].exists():
                write_json_atomic(paths["merged_transcript"], {"segments": merged})
            if not paths["metrics"].exists():
                write_metrics(
                    paths["metrics"],
                    plan,
                    time.perf_counter() - started_at,
                    cached,
                    len(merged),
                    [],
                )
            return _result(plan, merged)

    audio = decode_normalized_audio(audio_path)
    source = source_audio_fingerprint(audio_path, audio.sample_count, audio.sample_rate)
    if plan is not None and plan.source_audio != source:
        plan = None
        plan_options = None
    if plan is None:
        model_path = _resolve_model_path(options.model)
        plan_options = _options_with_model(options, model_path)
        workers = resolve_worker_config(
            audio.sample_count, os.cpu_count(), plan_options
        )
        vad_existed = paths["vad_result"].exists()
        speech = (
            load_valid_vad_result(paths["vad_result"], source, DEFAULT_VAD_PARAMETERS)
            if vad_existed
            else None
        )
        if speech is None:
            speech = detect_speech_intervals(audio, DEFAULT_VAD_PARAMETERS)
            write_vad_result(
                paths["vad_result"], source, DEFAULT_VAD_PARAMETERS, speech
            )
        plan = build_parallel_asr_plan(
            sample_count=audio.sample_count,
            cpu_count=os.cpu_count(),
            source_audio=source,
            options=plan_options,
            speech_intervals=speech,
            worker_config=workers,
        )
        write_plan(paths["plan"], plan)
        write_progress(paths["progress"], initial_progress(plan))
        status = "created"
    else:
        status = "reused"
    _log_plan(plan, status)
    assert plan_options is not None
    chunk_results = transcribe_whisper_chunks(plan, plan_options, workspace_dir, audio)
    merged = merge_chunk_results(plan, chunk_results)
    write_json_atomic(paths["merged_transcript"], {"segments": merged})
    progress = load_progress(paths["progress"])
    write_metrics(
        paths["metrics"],
        plan,
        time.perf_counter() - started_at,
        chunk_results,
        len(merged),
        failed_chunks_blocking_merge(progress),
    )
    return _result(plan, merged)


def _result(
    plan: ParallelAsrPlan,
    segments: list[dict[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]], str]:
    return (
        {
            "language": plan.language,
            "language_probability": None,
            "duration": plan.source_audio.duration,
            "duration_after_vad": None,
            "model": plan.model,
            "device": plan.device,
            "compute_type": plan.compute_type,
            "beam_size": plan.beam_size,
            "text_normalization": "simplified-chinese"
            if is_chinese_language(plan.language)
            else None,
        },
        segments,
        "faster-whisper",
    )
