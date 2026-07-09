from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.asr.common import (
    SIMPLIFIED_CHINESE_PROMPT,
    is_chinese_language,
    make_segment,
    normalize_segments_for_language,
)
from scripts.asr.parallel.plan import SCHEMA_VERSION, AsrChunkPlan, MacroChunkPlan, ParallelAsrPlan
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
from scripts.runtime_options import TranscribeOptions
from scripts.utils import path_to_posix


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
