from __future__ import annotations

import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

from scripts.alignment import AlignmentItem
from scripts.artifacts import write_workspace_result
from scripts.asr.alignment import (
    TranscriptWord,
)
from scripts.asr.chunking import (
    DEFAULT_VAD_PARAMETERS,
    NormalizedAudio,
    decode_normalized_audio,
    detect_speech_samples,
)
from scripts.asr.execution.base import ExecutionPolicy
from scripts.asr.merge import merge_chunk_transcripts
from scripts.asr.pipeline_types import (
    ASR_PIPELINE_SCHEMA_VERSION,
    AsrPipelinePlan,
    ChunkTranscript,
    SourceIdentity,
)
from scripts.asr.providers.base import AsrProvider
from scripts.asr.workspace import (
    chunk_key,
    chunk_payload,
    load_chunk_results,
    load_matching_plan,
    load_valid_vad_result,
    rebuild_progress,
    workspace_paths,
    write_vad_result,
)
from scripts.process_logging import get_logger, terminal_info
from scripts.utils import ensure_dir, write_json_atomic

logger = get_logger(__name__)

ProviderT = TypeVar("ProviderT", bound=AsrProvider)


def _metrics(
    plan: AsrPipelinePlan,
    results: dict[str, ChunkTranscript],
    elapsed: float,
    segment_count: int,
    provider_stage_seconds: float = 0.0,
) -> dict[str, Any]:
    speech_loads = [
        chunk.estimated_speech_samples / plan.source.sample_rate
        for chunk in plan.chunks
    ]
    mean = sum(speech_loads) / len(speech_loads)
    msre = (
        sum(((value - mean) / mean) ** 2 for value in speech_loads) / len(speech_loads)
        if mean
        else 0.0
    )
    identity = plan.execution_policy
    batch_size = int(identity.get("num_workers", identity.get("batch_size", 1)))
    return {
        "schema_version": ASR_PIPELINE_SCHEMA_VERSION,
        "provider": plan.provider_request["provider"],
        "execution_policy": identity["policy"],
        "total_elapsed_seconds": round(elapsed, 3),
        "provider_stage_seconds": round(provider_stage_seconds, 3),
        "chunk_elapsed_seconds": [
            {
                "chunk_index": transcript.chunk_index,
                "elapsed_seconds": transcript.elapsed_seconds,
            }
            for transcript in sorted(
                results.values(), key=lambda item: item.chunk_index
            )
        ],
        "chunk_count": len(plan.chunks),
        "batch_count": math.ceil(len(plan.chunks) / batch_size),
        "hard_cut_count": sum(chunk.end_boundary == "hard" for chunk in plan.chunks),
        "chunk_estimated_speech_durations": speech_loads,
        "max_estimated_speech_duration": max(speech_loads, default=0.0),
        "speech_load_msre": msre,
        "segment_count": segment_count,
        "failed_chunks": [],
        **{
            key: identity[key]
            for key in ("cpu_budget", "num_workers", "cpu_threads", "batch_size")
            if key in identity
        },
    }


def _complete_cache_output(
    *,
    provider: AsrProvider,
    plan: AsrPipelinePlan,
    results: dict[str, ChunkTranscript],
    paths: dict[str, Path],
    started: float,
    message: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    logger.info("ASR merge: start chunks=%d source=chunk_cache", len(plan.chunks))
    cached = merge_chunk_transcripts(plan, results)
    _write_result(paths["result"], plan, cached[0], cached[1])
    logger.info(
        "ASR merge: complete chunks=%d segments=%d source=chunk_cache",
        len(plan.chunks),
        len(cached[2]),
    )
    terminal_info(logger, message)
    _, words, raw_segments = cached
    if not paths["metrics"].exists():
        write_json_atomic(
            paths["metrics"],
            _metrics(
                plan,
                results,
                time.perf_counter() - started,
                len(raw_segments),
            ),
        )
    return (
        provider.final_info(plan, bool(words)),
        provider.postprocess_segments(raw_segments),
        provider.source,
    )


def _write_result(
    path: Path, plan: AsrPipelinePlan, text: str, words: list[TranscriptWord]
) -> None:
    write_workspace_result(
        path,
        text=text,
        items=[
            AlignmentItem(word.text, word.start, word.end, word.probability)
            for word in words
        ],
        duration=plan.source.duration,
        provider=str(plan.provider_request["provider"]),
        language=str(plan.provider_request["language"]),
    )


def _run_asr_pipeline(
    audio_path: Path,
    workspace_dir: Path,
    provider: ProviderT,
    policy: ExecutionPolicy[ProviderT],
    *,
    prepared_audio: NormalizedAudio | None = None,
    prepared_vad: list[tuple[int, int]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    started = time.perf_counter()
    paths = workspace_paths(workspace_dir)
    request = provider.request_identity()
    plan = load_matching_plan(
        paths["plan"],
        audio_path,
        request,
        policy.execution_identity,
        DEFAULT_VAD_PARAMETERS,
        policy.planning_parameters,
    )
    logger.info(
        "ASR plan cache: status=%s workspace=%s",
        "hit" if plan is not None else "miss",
        workspace_dir,
    )
    cached_plan = plan
    results: dict[str, ChunkTranscript] = {}
    vad_intervals: list[tuple[int, int]] | None = None
    vad_needs_regeneration = plan is None

    if plan is not None:
        vad = load_valid_vad_result(
            paths["vad"],
            plan.source,
            DEFAULT_VAD_PARAMETERS,
        )
        if vad.reason is not None:
            vad_needs_regeneration = True
            logger.warning(
                "ASR VAD cache invalid: workspace=%s reason=%s; regenerating VAD.",
                workspace_dir,
                vad.reason,
            )
        else:
            vad_intervals = vad.intervals
            logger.info(
                "ASR VAD cache: status=valid intervals=%d",
                len(vad_intervals or []),
            )
            derived_layouts = policy.layouts(
                plan.source.sample_count,
                vad_intervals or [],
                plan.execution_policy,
            )
            if derived_layouts != plan.chunks:
                logger.warning(
                    "ASR VAD cache invalidates plan layouts: "
                    "workspace=%s reason=layout_mismatch; rebuilding plan.",
                    workspace_dir,
                )
                plan = replace(plan, chunks=derived_layouts)
                plan.validate()
                ensure_dir(workspace_dir)
                write_json_atomic(paths["plan"], plan.to_dict())
                logger.info(
                    "ASR plan: status=rebuilt reason=layout_mismatch chunks=%d",
                    len(plan.chunks),
                )
            else:
                logger.info(
                    "ASR plan: status=reused chunks=%d",
                    len(plan.chunks),
                )
                results = load_chunk_results(workspace_dir, plan)
                if len(results) == len(plan.chunks):
                    return _complete_cache_output(
                        provider=provider,
                        plan=plan,
                        results=results,
                        paths=paths,
                        started=started,
                        message=(
                            "[Transcribe] cache: complete; skipped audio decode, "
                            "device check, and model load"
                        ),
                    )

    logger.info("ASR audio decode: start")
    audio = prepared_audio or decode_normalized_audio(audio_path)
    logger.info("ASR audio decode: complete samples=%d", audio.sample_count)

    if plan is not None and plan.source.sample_count != audio.sample_count:
        logger.warning(
            "ASR VAD cache invalid: workspace=%s reason=source_mismatch; "
            "cached_sample_count=%d decoded_sample_count=%d; regenerating VAD.",
            workspace_dir,
            plan.source.sample_count,
            audio.sample_count,
        )
        vad_needs_regeneration = True
        vad_intervals = None

    if vad_needs_regeneration:
        source = SourceIdentity.from_path(audio_path, audio.sample_count)
        logger.info("ASR VAD: start")
        speech = (
            prepared_vad
            if prepared_vad is not None
            else detect_speech_samples(audio, DEFAULT_VAD_PARAMETERS)
        )
        logger.info(
            "ASR VAD: complete intervals=%d speech_samples=%d",
            len(speech),
            sum(end - start for start, end in speech),
        )
        execution_identity = policy.execution_identity(audio.sample_count)
        rebuilt_plan = AsrPipelinePlan(
            ASR_PIPELINE_SCHEMA_VERSION,
            source,
            request,
            execution_identity,
            DEFAULT_VAD_PARAMETERS,
            policy.planning_parameters,
            policy.layouts(audio.sample_count, speech, execution_identity),
        )
        rebuilt_plan.validate()
        ensure_dir(workspace_dir)
        if (
            cached_plan is not None
            and cached_plan.source.sample_count == audio.sample_count
            and rebuilt_plan.chunks == cached_plan.chunks
        ):
            plan = cached_plan
            results = load_chunk_results(workspace_dir, plan)
            logger.info(
                "ASR plan: status=reused_after_vad_repair chunks=%d",
                len(plan.chunks),
            )
        else:
            plan = rebuilt_plan
            results = {}
            write_json_atomic(paths["plan"], plan.to_dict())
            logger.info(
                "ASR plan: status=%s chunks=%d",
                "created" if cached_plan is None else "rebuilt",
                len(plan.chunks),
            )
        write_vad_result(paths["vad"], plan, speech)
    elif plan is None:
        raise RuntimeError("ASR pipeline lost its plan during VAD cache recovery.")

    if len(results) == len(plan.chunks):
        return _complete_cache_output(
            provider=provider,
            plan=plan,
            results=results,
            paths=paths,
            started=started,
            message=(
                "[Transcribe] cache: complete after VAD repair; "
                "skipped device check and model load"
            ),
        )

    pending = [
        layout for layout in plan.chunks if chunk_key(layout.index) not in results
    ]
    write_json_atomic(paths["progress"], rebuild_progress(plan, results))

    def cache(transcript: ChunkTranscript) -> None:
        key = chunk_key(transcript.chunk_index)
        write_json_atomic(
            paths["chunks"] / f"{key}.json", chunk_payload(plan, transcript)
        )
        results[key] = transcript
        write_json_atomic(paths["progress"], rebuild_progress(plan, results))

    terminal_info(
        logger,
        "[Transcribe] cache: reused=%d, pending=%d, total=%d",
        len(results),
        len(pending),
        len(plan.chunks),
    )
    logger.info(
        "ASR execution: start provider=%s policy=%s pending=%d",
        provider.name,
        policy.name,
        len(pending),
    )
    provider_started = time.perf_counter()
    failures = policy.execute(provider, audio, pending, plan.execution_policy, cache)
    provider_stage_seconds = time.perf_counter() - provider_started
    logger.info(
        "ASR execution: complete provider=%s policy=%s succeeded=%d failed=%d",
        provider.name,
        policy.name,
        len(results),
        len(failures),
    )
    if failures:
        summaries = {
            key: f"{type(exc).__name__}: {exc}" for key, exc in failures.items()
        }
        write_json_atomic(paths["progress"], rebuild_progress(plan, results, summaries))
        details = "; ".join(f"{key}: {summaries[key]}" for key in sorted(summaries))
        error = RuntimeError(f"ASR chunks failed after retry/isolation: {details}")
        if len(failures) == 1:
            raise error from next(iter(failures.values()))
        raise error
    logger.info("ASR merge: start chunks=%d source=execution", len(plan.chunks))
    text, words, raw_segments = merge_chunk_transcripts(plan, results)
    logger.info(
        "ASR merge: complete chunks=%d segments=%d source=execution",
        len(plan.chunks),
        len(raw_segments),
    )
    _write_result(paths["result"], plan, text, words)
    write_json_atomic(
        paths["metrics"],
        _metrics(
            plan,
            results,
            time.perf_counter() - started,
            len(raw_segments),
            provider_stage_seconds,
        ),
    )
    return (
        provider.final_info(plan, bool(words)),
        provider.postprocess_segments(raw_segments),
        provider.source,
    )


def run_asr_pipeline(
    audio_path: Path,
    workspace_dir: Path,
    provider: ProviderT,
    policy: ExecutionPolicy[ProviderT],
    *,
    prepared_audio: NormalizedAudio | None = None,
    prepared_vad: list[tuple[int, int]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    started = time.perf_counter()
    logger.info(
        "ASR pipeline start: provider=%s policy=%s workspace=%s",
        provider.name,
        policy.name,
        workspace_dir,
    )
    try:
        result = _run_asr_pipeline(
            audio_path,
            workspace_dir,
            provider,
            policy,
            prepared_audio=prepared_audio,
            prepared_vad=prepared_vad,
        )
    except Exception as exc:
        logger.error(
            "ASR pipeline failure: provider=%s policy=%s error=%s: %s",
            provider.name,
            policy.name,
            type(exc).__name__,
            exc,
        )
        raise
    logger.info(
        "ASR pipeline success: provider=%s policy=%s elapsed_seconds=%.3f",
        provider.name,
        policy.name,
        time.perf_counter() - started,
    )
    return result
