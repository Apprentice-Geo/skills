from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from scripts.asr.alignment import (
    TranscriptWord,
    build_sentence_segments,
    validate_alignment_contract,
)
from scripts.asr.chunking import (
    DEFAULT_VAD_PARAMETERS,
    decode_normalized_audio,
    detect_speech_samples,
)
from scripts.asr.merge import merge_chunk_transcripts, merged_payload
from scripts.asr.pipeline_types import (
    ASR_PIPELINE_SCHEMA_VERSION,
    AsrPipelinePlan,
    ChunkTranscript,
    SourceIdentity,
)
from scripts.asr.workspace import (
    chunk_key,
    chunk_payload,
    load_chunk_results,
    load_matching_plan,
    rebuild_progress,
    workspace_paths,
    write_vad_result,
)
from scripts.process_logging import get_logger, terminal_info
from scripts.utils import ensure_dir, write_json_atomic

logger = get_logger(__name__)


def _metrics(
    plan: AsrPipelinePlan,
    results: dict[str, ChunkTranscript],
    elapsed: float,
    segment_count: int,
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


def _load_valid_merged(
    path: Path, plan: AsrPipelinePlan
) -> tuple[str, list[TranscriptWord], list[dict[str, Any]]] | None:
    from scripts.asr.workspace import load_json_or_none

    data = load_json_or_none(path)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != ASR_PIPELINE_SCHEMA_VERSION
        or data.get("plan") != plan.to_dict()
        or not isinstance(data.get("text"), str)
        or not isinstance(data.get("words"), list)
        or not isinstance(data.get("segments"), list)
    ):
        return None
    try:
        words = [TranscriptWord(**item) for item in data["words"]]
        validate_alignment_contract(
            data["text"],
            words,
            plan.source.duration,
            chunk_index="merged-cache",
            language=str(plan.provider_request["language"]),
        )
        segments = list(data["segments"])
        if any(
            not isinstance(segment, dict)
            or segment.get("id") != index
            or not isinstance(segment.get("text"), str)
            for index, segment in enumerate(segments)
        ):
            return None
        expected_segments = build_sentence_segments(
            data["text"],
            words,
            plan.source.duration,
            chunk_index="merged-cache",
            language=str(plan.provider_request["language"]),
        )
        if segments != expected_segments:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return data["text"], words, segments


def run_asr_pipeline(
    audio_path: Path,
    workspace_dir: Path,
    provider: Any,
    policy: Any,
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
    results = load_chunk_results(workspace_dir, plan) if plan is not None else {}
    if plan is not None and len(results) == len(plan.chunks):
        cached = _load_valid_merged(paths["result"], plan)
        if cached is None:
            cached = merge_chunk_transcripts(plan, results)
            write_json_atomic(paths["result"], merged_payload(plan, *cached))
        terminal_info(
            logger,
            "[Transcribe] cache: complete; skipped audio decode, device check, and model load",
        )
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

    audio = decode_normalized_audio(audio_path)
    if plan is None or plan.source.sample_count != audio.sample_count:
        source = SourceIdentity.from_path(audio_path, audio.sample_count)
        speech = detect_speech_samples(audio, DEFAULT_VAD_PARAMETERS)
        execution_identity = policy.execution_identity(audio.sample_count)
        plan = AsrPipelinePlan(
            ASR_PIPELINE_SCHEMA_VERSION,
            source,
            request,
            execution_identity,
            DEFAULT_VAD_PARAMETERS,
            policy.planning_parameters,
            policy.layouts(audio.sample_count, speech, execution_identity),
        )
        plan.validate()
        ensure_dir(workspace_dir)
        write_json_atomic(paths["plan"], plan.to_dict())
        write_vad_result(paths["vad"], plan, speech)
        results = {}
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
    failures = policy.execute(provider, audio, pending, plan.execution_policy, cache)
    if failures:
        write_json_atomic(paths["progress"], rebuild_progress(plan, results, failures))
        raise RuntimeError(
            f"ASR chunks failed after retry/isolation: {', '.join(sorted(failures))}"
        )
    text, words, raw_segments = merge_chunk_transcripts(plan, results)
    write_json_atomic(paths["result"], merged_payload(plan, text, words, raw_segments))
    write_json_atomic(
        paths["metrics"],
        _metrics(plan, results, time.perf_counter() - started, len(raw_segments)),
    )
    return (
        provider.final_info(plan, bool(words)),
        provider.postprocess_segments(raw_segments),
        provider.source,
    )
