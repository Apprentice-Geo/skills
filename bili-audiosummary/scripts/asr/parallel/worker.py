from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
    chunk_path: Path,
    plan: ParallelAsrPlan,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    segments, info = model.transcribe(
        path_to_posix(chunk_path),
        language=plan.language,
        beam_size=plan.beam_size,
        vad_filter=True,
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
    chunk: AsrChunkPlan,
    chunk_path: Path,
    elapsed_seconds: float,
    info: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "chunk_index": chunk.index,
        "start": chunk.start,
        "duration": chunk.duration,
        "end_boundary": chunk.end_boundary,
        "source": asdict(plan.source_audio),
        "plan": asdict(plan),
        "chunk_audio_path": path_to_posix(chunk_path),
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


def _submit_pending_chunks(
    executor: ThreadPoolExecutor,
    model: Any,
    plan: ParallelAsrPlan,
    workspace_dir: Path,
    progress_path: Path,
    progress: dict[str, Any],
    chunk_results: dict[str, dict[str, Any]],
) -> None:
    pending = [
        chunk
        for chunk in plan.chunks
        if progress["chunks"][chunk_key(chunk)]["status"] == "pending"
    ]
    if not pending:
        return

    def run_one(
        chunk: AsrChunkPlan,
    ) -> tuple[AsrChunkPlan, float, dict[str, Any], list[dict[str, Any]]]:
        started_at = time.perf_counter()
        info, segments = _transcribe_chunk(
            model,
            workspace_dir / chunk.path,
            plan,
        )
        return chunk, time.perf_counter() - started_at, info, segments

    futures: dict[Future, AsrChunkPlan] = {}
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
            logger.warning("ASR chunk %s failed", key, exc_info=True)
            item.update(
                {
                    "status": "failed",
                    "retry_count": retry_count,
                    "error": str(exc),
                }
            )
            write_progress(progress_path, progress)
            if retry_count < MAX_CHUNK_RETRIES:
                terminal_info(
                    logger,
                    "[Transcribe] %s failed; retrying (%d/%d): %s",
                    key,
                    retry_count + 1,
                    MAX_CHUNK_RETRIES,
                    exc,
                )
                item.update(
                    {
                        "status": "pending",
                        "retry_count": retry_count + 1,
                        "error": None,
                    }
                )
                write_progress(progress_path, progress)
            else:
                terminal_info(
                    logger,
                    "[Transcribe] %s failed after %d retry: %s",
                    key,
                    retry_count,
                    exc,
                )
            continue

        result = _chunk_result_payload(
            plan,
            finished_chunk,
            workspace_dir / finished_chunk.path,
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
        terminal_info(logger, "[Transcribe] %s succeeded", key)


def transcribe_whisper_chunks(
    plan: ParallelAsrPlan,
    options: TranscribeOptions,
    workspace_dir: Path,
) -> dict[str, dict[str, Any]]:
    paths = workspace_paths(workspace_dir)
    progress_path = paths["progress"]
    progress_existed = progress_path.exists()
    valid_results = load_valid_chunk_results(workspace_dir, plan)
    result_file_count = len(list(paths["chunk_results"].glob("*.json")))
    progress = None
    if progress_existed:
        try:
            progress = load_progress(progress_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Invalid ASR progress; rebuilding: %s", exc)
    previous_chunks = (
        {key: dict(item) for key, item in progress.get("chunks", {}).items()}
        if progress is not None
        else {}
    )
    progress = prepare_progress_for_resume(plan, progress, set(valid_results))
    write_progress(progress_path, progress)

    pending_count = sum(
        progress["chunks"][chunk_key(chunk)]["status"] == "pending"
        for chunk in plan.chunks
    )
    terminal_info(
        logger,
        "[Transcribe] cache: reused=%d, ignored=%d, pending=%d, total=%d",
        len(valid_results),
        max(0, result_file_count - len(valid_results)),
        pending_count,
        len(plan.chunks),
    )
    for chunk in plan.chunks:
        key = chunk_key(chunk)
        if key in valid_results:
            terminal_info(logger, "[Transcribe] %s reused cached result", key)
            continue
        if not progress_existed:
            continue
        previous = previous_chunks.get(key, {})
        status = previous.get("status")
        retry_count = int(previous.get("retry_count", 0))
        if status == "running":
            terminal_info(logger, "[Transcribe] %s resumed after interruption", key)
        elif retry_count > 0 or status == "failed":
            terminal_info(
                logger,
                "[Transcribe] %s resumed with a new retry budget (%d/%d)",
                key,
                MAX_CHUNK_RETRIES,
                MAX_CHUNK_RETRIES,
            )

    if not any(
        progress["chunks"][chunk_key(chunk)]["status"] == "pending"
        for chunk in plan.chunks
    ):
        return valid_results

    from faster_whisper import WhisperModel

    model_path = _resolve_model_path(plan.model or options.model)
    model = WhisperModel(
        model_path,
        device=plan.device,
        compute_type=plan.compute_type,
        cpu_threads=plan.cpu_threads,
        num_workers=plan.num_workers,
    )
    chunk_results = dict(valid_results)
    with ThreadPoolExecutor(max_workers=plan.num_workers) as executor:
        while True:
            _submit_pending_chunks(
                executor,
                model,
                plan,
                workspace_dir,
                progress_path,
                progress,
                chunk_results,
            )
            blocking = failed_chunks_blocking_merge(progress)
            if blocking:
                raise RuntimeError(f"ASR chunk failed after retry: {', '.join(blocking)}")
            if not any(
                progress["chunks"][chunk_key(chunk)]["status"] == "pending"
                for chunk in plan.chunks
            ):
                break
    return chunk_results
