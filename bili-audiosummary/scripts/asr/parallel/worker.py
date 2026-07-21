from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.asr.chunking import NormalizedAudio
from scripts.asr.common import make_segment, normalize_segments_for_language
from scripts.asr.parallel.plan import SCHEMA_VERSION, AsrChunkPlan, ParallelAsrPlan
from scripts.asr.parallel.state import (
    MAX_CHUNK_RETRIES,
    chunk_key,
    chunk_result_path,
    failed_chunks_blocking_merge,
    load_progress,
    load_valid_chunk_results,
    prepare_progress_for_resume,
    workspace_paths,
    write_chunk_result_atomic,
    write_progress,
)
from scripts.config import DEFAULT_WHISPER_MODEL_DIR
from scripts.process_logging import get_logger, terminal_info
from scripts.runtime_options import TranscribeOptions
from scripts.utils import path_to_posix

logger = get_logger(__name__)


def _resolve_model_path(model: str | None) -> str:
    if model:
        return model
    if (DEFAULT_WHISPER_MODEL_DIR / "model.bin").exists():
        return path_to_posix(DEFAULT_WHISPER_MODEL_DIR)
    raise RuntimeError(
        "Local faster-whisper model is missing. Run "
        r"uv run --no-sync python -m scripts.setup.install_model --model faster-whisper "
        "before using faster-whisper ASR."
    )


def _transcribe_chunk(
    model: Any,
    samples: Any,
    plan: ParallelAsrPlan,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    segments, info = model.transcribe(
        samples,
        language=plan.language,
        beam_size=plan.beam_size,
        vad_filter=True,
    )
    segment_list = normalize_segments_for_language(
        [make_segment(segment) for segment in segments],
        plan.language,
    )
    return (
        {
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "duration_after_vad": getattr(info, "duration_after_vad", None),
        },
        segment_list,
    )


def _chunk_result_payload(
    plan: ParallelAsrPlan,
    chunk: AsrChunkPlan,
    elapsed_seconds: float,
    info: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "chunk_index": chunk.index,
        "start_sample": chunk.start_sample,
        "end_sample": chunk.end_sample,
        "end_boundary": chunk.end_boundary,
        "source": asdict(plan.source_audio),
        "plan": asdict(plan),
        "model": {
            "path": plan.model,
            "language": plan.language,
            "beam_size": plan.beam_size,
            "device": plan.device,
            "compute_type": plan.compute_type,
            "cpu_threads": plan.cpu_threads,
            "num_workers": plan.num_workers,
        },
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "info": info,
        "segments": segments,
    }


def transcribe_whisper_chunks(
    plan: ParallelAsrPlan,
    options: TranscribeOptions,
    workspace_dir: Path,
    audio: NormalizedAudio | None = None,
) -> dict[str, dict[str, Any]]:
    paths = workspace_paths(workspace_dir)
    progress_path = paths["progress"]
    valid_results = load_valid_chunk_results(workspace_dir, plan)
    progress = None
    if progress_path.exists():
        try:
            progress = load_progress(progress_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Invalid ASR progress; rebuilding: %s", exc)
    progress = prepare_progress_for_resume(plan, progress, set(valid_results))
    write_progress(progress_path, progress)
    pending = [chunk for chunk in plan.chunks if chunk_key(chunk) not in valid_results]
    terminal_info(
        logger,
        "[Transcribe] cache: reused=%d, pending=%d, total=%d",
        len(valid_results),
        len(pending),
        len(plan.chunks),
    )
    if not pending:
        return valid_results
    if audio is None:
        raise ValueError("Normalized audio is required for pending Whisper chunks.")

    from faster_whisper import WhisperModel

    model_path = _resolve_model_path(plan.model or options.model)
    model = WhisperModel(
        model_path,
        device=plan.device,
        compute_type=plan.compute_type,
        cpu_threads=plan.cpu_threads,
        num_workers=plan.num_workers,
    )
    results = dict(valid_results)

    def run_one(chunk: AsrChunkPlan):
        started = time.perf_counter()
        info, segments = _transcribe_chunk(
            model,
            audio.samples[chunk.start_sample : chunk.end_sample],
            plan,
        )
        return time.perf_counter() - started, info, segments

    with ThreadPoolExecutor(max_workers=plan.num_workers) as executor:
        while True:
            current = [
                chunk
                for chunk in plan.chunks
                if progress["chunks"][chunk_key(chunk)]["status"] == "pending"
            ]
            if not current:
                break
            futures: dict[Future, AsrChunkPlan] = {}
            for chunk in current:
                key = chunk_key(chunk)
                progress["chunks"][key]["status"] = "running"
                write_progress(progress_path, progress)
                futures[executor.submit(run_one, chunk)] = chunk
            for future in as_completed(futures):
                chunk = futures[future]
                key = chunk_key(chunk)
                try:
                    elapsed, info, segments = future.result()
                except Exception as exc:
                    item = progress["chunks"][key]
                    retries = int(item.get("retry_count", 0))
                    logger.warning("ASR chunk %s failed", key, exc_info=True)
                    if retries < MAX_CHUNK_RETRIES:
                        item.update(
                            status="pending", retry_count=retries + 1, error=None
                        )
                    else:
                        item.update(
                            status="failed", retry_count=retries, error=str(exc)
                        )
                    write_progress(progress_path, progress)
                    continue
                payload = _chunk_result_payload(plan, chunk, elapsed, info, segments)
                write_chunk_result_atomic(
                    chunk_result_path(workspace_dir, chunk), payload
                )
                results[key] = payload
                progress["chunks"][key].update(status="succeeded", error=None)
                write_progress(progress_path, progress)
            blocking = failed_chunks_blocking_merge(progress)
            if blocking:
                raise RuntimeError(
                    f"ASR chunk failed after retry: {', '.join(blocking)}"
                )
    return results
