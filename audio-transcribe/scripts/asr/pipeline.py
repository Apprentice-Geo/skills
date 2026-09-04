from __future__ import annotations

import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, TypeVar

from scripts.artifacts import write_workspace_result
from scripts.asr.alignment import (
    ALIGNMENT_POLICY,
    AlignedTranscript,
    accept_provider_transcript,
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
    AsrPipelinePlan,
    ChunkTranscript,
    PipelineMetrics,
    PipelineOutcome,
    SourceIdentity,
)
from scripts.asr.providers.base import AsrProvider
from scripts.asr.workspace import (
    chunk_key,
    chunk_payload,
    load_chunk_results,
    load_matching_plan,
    load_valid_vad_result,
    validate_vad_intervals,
    workspace_paths,
    write_vad_result,
)
from scripts.process_logging import get_logger, terminal_info
from scripts.utils import ensure_dir, write_json_atomic

logger = get_logger(__name__)

ProviderT = TypeVar("ProviderT", bound=AsrProvider)


def _metrics(
    plan: AsrPipelinePlan,
    elapsed: float,
    chunk_elapsed_seconds: dict[int, float],
    provider_stage_seconds: float = 0.0,
) -> PipelineMetrics:
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
    return PipelineMetrics(
        provider=str(plan.provider_request["provider"]),
        execution_policy=str(identity["policy"]),
        total_elapsed_seconds=round(elapsed, 3),
        provider_stage_seconds=round(provider_stage_seconds, 3),
        chunk_elapsed_seconds=tuple(
            {
                "chunk_index": chunk_index,
                "elapsed_seconds": chunk_elapsed_seconds[chunk_index],
            }
            for chunk_index in sorted(chunk_elapsed_seconds)
        ),
        chunk_count=len(plan.chunks),
        batch_count=math.ceil(len(plan.chunks) / batch_size),
        hard_cut_count=sum(chunk.end_boundary == "hard" for chunk in plan.chunks),
        chunk_estimated_speech_durations=tuple(speech_loads),
        max_estimated_speech_duration=max(speech_loads, default=0.0),
        speech_load_msre=msre,
        cpu_budget=identity.get("cpu_budget"),
        num_workers=identity.get("num_workers"),
        cpu_threads=identity.get("cpu_threads"),
        batch_size=identity.get("batch_size"),
    )


def _log_cleanup_report(
    provider: str,
    transcript: ChunkTranscript,
    warned_chunks: set[int],
) -> None:
    report = transcript.cleanup_report
    if (
        report.dropped_zero_duration_items == 0
        or transcript.chunk_index in warned_chunks
    ):
        return
    warned_chunks.add(transcript.chunk_index)
    logger.warning(
        "ASR timestamp cleanup: provider=%s chunk=%s "
        "action=drop_zero_duration_items dropped=%d "
        "first_start=%.3f last_end=%.3f",
        provider,
        chunk_key(transcript.chunk_index),
        report.dropped_zero_duration_items,
        report.first_start,
        report.last_end,
    )


def _complete_cache_output(
    *,
    provider: AsrProvider,
    plan: AsrPipelinePlan,
    results: dict[str, ChunkTranscript],
    paths: dict[str, Path],
    started: float,
    message: str,
    audio_id: str,
    variant_id: str,
) -> PipelineOutcome:
    logger.info("ASR merge: start chunks=%d source=chunk_cache", len(plan.chunks))
    alignment = merge_chunk_transcripts(plan, results)
    _write_result(paths["result"], plan, alignment, audio_id, variant_id)
    logger.info(
        "ASR merge: complete chunks=%d source=chunk_cache",
        len(plan.chunks),
    )
    terminal_info(logger, message)
    return PipelineOutcome(
        final_info=provider.final_info(plan, bool(alignment.items)),
        source=provider.source,
        metrics=_metrics(plan, time.perf_counter() - started, {}),
    )


def _write_result(
    path: Path,
    plan: AsrPipelinePlan,
    alignment: AlignedTranscript,
    audio_id: str,
    variant_id: str,
) -> None:
    write_workspace_result(
        path,
        audio_id=audio_id,
        variant_id=variant_id,
        text=alignment.text,
        items=list(alignment.items),
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
    audio_id: str,
    variant_id: str,
    prepared_audio: NormalizedAudio | None = None,
    prepared_vad: list[tuple[int, int]] | None = None,
    vad_detector: Callable[[NormalizedAudio], list[tuple[int, int]]] | None = None,
) -> PipelineOutcome:
    started = time.perf_counter()
    warned_cleanup_chunks: set[int] = set()
    paths = workspace_paths(workspace_dir)
    request = {
        **provider.request_identity(),
        "alignment_policy": dict(ALIGNMENT_POLICY),
    }
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
    results: dict[str, ChunkTranscript] = {}
    if plan is not None:
        if plan.source.audio_id != audio_id:
            raise ValueError("ASR plan does not match the expected audio identity.")
        logger.info("ASR plan: status=reused chunks=%d", len(plan.chunks))
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
                audio_id=audio_id,
                variant_id=variant_id,
            )

    logger.info("ASR audio decode: start")
    audio = prepared_audio or decode_normalized_audio(audio_path)
    logger.info("ASR audio decode: complete samples=%d", audio.sample_count)

    if plan is not None and plan.source.sample_count != audio.sample_count:
        logger.warning(
            "ASR plan cache invalid after decode: workspace=%s reason=source_mismatch; "
            "cached_sample_count=%d decoded_sample_count=%d; regenerating VAD.",
            workspace_dir,
            plan.source.sample_count,
            audio.sample_count,
        )
        plan = None
        results = {}

    if plan is None:
        source = SourceIdentity.from_path(audio_path, audio.sample_count)
        if source.audio_id != audio_id:
            raise ValueError(
                "Decoded source does not match the expected audio identity."
            )
        vad_was_created = False
        if prepared_vad is not None:
            speech = validate_vad_intervals(prepared_vad, source)
            vad_was_created = True
            logger.info("ASR VAD: status=prepared intervals=%d", len(speech))
        else:
            cached_vad = load_valid_vad_result(
                paths["vad"], source, DEFAULT_VAD_PARAMETERS
            )
            if cached_vad.reason is None:
                speech = cached_vad.intervals or []
                logger.info("ASR VAD cache: status=valid intervals=%d", len(speech))
            else:
                logger.warning(
                    "ASR VAD cache invalid: workspace=%s reason=%s; regenerating VAD.",
                    workspace_dir,
                    cached_vad.reason,
                )
                logger.info("ASR VAD: start")
                detected = (
                    vad_detector(audio)
                    if vad_detector is not None
                    else detect_speech_samples(audio, DEFAULT_VAD_PARAMETERS)
                )
                speech = validate_vad_intervals(detected, source)
                vad_was_created = True
                logger.info(
                    "ASR VAD: complete intervals=%d speech_samples=%d",
                    len(speech),
                    sum(end - start for start, end in speech),
                )
        ensure_dir(workspace_dir)
        if vad_was_created:
            write_vad_result(paths["vad"], source, DEFAULT_VAD_PARAMETERS, speech)
        execution_identity = policy.execution_identity(audio.sample_count)
        plan = AsrPipelinePlan(
            source,
            request,
            execution_identity,
            DEFAULT_VAD_PARAMETERS,
            policy.planning_parameters,
            policy.layouts(audio.sample_count, speech, execution_identity),
        )
        plan.validate()
        write_json_atomic(paths["plan"], plan.to_dict())
        logger.info("ASR plan: status=created chunks=%d", len(plan.chunks))

    pending = [
        layout for layout in plan.chunks if chunk_key(layout.index) not in results
    ]
    current_chunk_elapsed: dict[int, float] = {}

    def cache(transcript: ChunkTranscript) -> None:
        transcript.validate_metadata()
        try:
            layout = plan.chunks[transcript.chunk_index]
        except IndexError as exc:
            raise RuntimeError("Provider returned an unknown ASR chunk.") from exc
        if (
            transcript.chunk_index != layout.index
            or transcript.start_sample != layout.start_sample
            or transcript.end_sample != layout.end_sample
        ):
            raise RuntimeError("Provider returned mismatched ASR chunk identity.")
        accepted, report = accept_provider_transcript(
            AlignedTranscript(transcript.text, transcript.words),
            duration=layout.sample_count / plan.source.sample_rate,
            chunk_index=layout.index,
            language=str(plan.provider_request["language"]),
        )
        transcript = replace(
            transcript,
            text=accepted.text,
            words=accepted.items,
            cleanup_report=report,
        )
        _log_cleanup_report(provider.name, transcript, warned_cleanup_chunks)
        current_chunk_elapsed[transcript.chunk_index] = transcript.elapsed_seconds
        key = chunk_key(transcript.chunk_index)
        write_json_atomic(
            paths["chunks"] / f"{key}.json", chunk_payload(plan, transcript)
        )
        results[key] = transcript

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
        details = "; ".join(f"{key}: {summaries[key]}" for key in sorted(summaries))
        error = RuntimeError(f"ASR chunks failed after retry/isolation: {details}")
        if len(failures) == 1:
            raise error from next(iter(failures.values()))
        raise error
    logger.info("ASR merge: start chunks=%d source=execution", len(plan.chunks))
    alignment = merge_chunk_transcripts(plan, results)
    logger.info(
        "ASR merge: complete chunks=%d source=execution",
        len(plan.chunks),
    )
    _write_result(paths["result"], plan, alignment, audio_id, variant_id)
    return PipelineOutcome(
        final_info=provider.final_info(plan, bool(alignment.items)),
        source=provider.source,
        metrics=_metrics(
            plan,
            time.perf_counter() - started,
            current_chunk_elapsed,
            provider_stage_seconds,
        ),
    )


def run_asr_pipeline(
    audio_path: Path,
    workspace_dir: Path,
    provider: ProviderT,
    policy: ExecutionPolicy[ProviderT],
    *,
    audio_id: str,
    variant_id: str,
    prepared_audio: NormalizedAudio | None = None,
    prepared_vad: list[tuple[int, int]] | None = None,
    vad_detector: Callable[[NormalizedAudio], list[tuple[int, int]]] | None = None,
) -> PipelineOutcome:
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
            audio_id=audio_id,
            variant_id=variant_id,
            prepared_audio=prepared_audio,
            prepared_vad=prepared_vad,
            vad_detector=vad_detector,
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
