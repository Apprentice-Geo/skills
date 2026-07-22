from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.asr.chunking import (
    SAMPLE_RATE,
    decode_normalized_audio,
    detect_speech_samples,
)
from scripts.asr.qwen3_alignment import (
    MAX_SEGMENT_CHARACTERS,
    MAX_SEGMENT_SECONDS,
    MIN_SEGMENT_SECONDS,
    STRONG_PUNCTUATION,
    TARGET_SEGMENT_SECONDS,
    WEAK_PUNCTUATION,
    AlignmentContractError,
    AlignmentItem,
    _to_float,
    build_sentence_segments,
    normalize_alignment_items,
    validate_alignment_contract,
)
from scripts.asr.qwen3_merge import _qwen_merge
from scripts.asr.qwen3_plan import (
    DEFAULT_VAD_PARAMETERS,
    QWEN3_CACHE_SCHEMA_VERSION,
    QWEN3_LANGUAGE_NAMES,
    SAMPLE_PLANNING_PARAMETERS,
    _qwen_build_plan,
    _qwen_language_name,
    _qwen_request_identity,
    _qwen_source_identity,
    _qwen_validate_plan,
)
from scripts.asr.qwen3_workspace import (
    QWEN3_SEGMENTATION_VERSION,
    _qwen_chunk_key,
    _qwen_load_chunk_results,
    _qwen_load_json,
    _qwen_progress,
    _qwen_valid_alignment,
    _qwen_workspace_paths,
)
from scripts.asr.qwen3_workspace import (
    _qwen_load_merged as _load_merged,
)
from scripts.config import (
    DEFAULT_HF_ENDPOINT,
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ASR_MODEL_DIR,
    QWEN3_DEVICE_MAP,
    QWEN3_DTYPE,
    QWEN3_MAX_INFERENCE_BATCH_SIZE,
    QWEN3_MAX_NEW_TOKENS,
)
from scripts.process_logging import get_logger, terminal_info
from scripts.utils import ensure_dir, path_to_posix, write_json_atomic

logger = get_logger(__name__)

# Keep the historical scripts.asr.qwen3 import surface while implementation
# responsibilities live in focused sibling modules.
__all__ = [
    "AlignmentItem",
    "AlignmentContractError",
    "DEFAULT_VAD_PARAMETERS",
    "MIN_SEGMENT_SECONDS",
    "MAX_SEGMENT_SECONDS",
    "MAX_SEGMENT_CHARACTERS",
    "QWEN3_CACHE_SCHEMA_VERSION",
    "QWEN3_SEGMENTATION_VERSION",
    "QWEN3_LANGUAGE_NAMES",
    "SAMPLE_PLANNING_PARAMETERS",
    "STRONG_PUNCTUATION",
    "TARGET_SEGMENT_SECONDS",
    "WEAK_PUNCTUATION",
    "_qwen_build_plan",
    "_qwen_chunk_key",
    "_qwen_language_name",
    "_qwen_load_chunk_results",
    "_qwen_load_json",
    "_qwen_load_merged",
    "_qwen_merge",
    "_qwen_progress",
    "_qwen_request_identity",
    "_qwen_result_payload",
    "_qwen_source_identity",
    "_qwen_valid_alignment",
    "_qwen_validate_plan",
    "_qwen_workspace_paths",
    "_to_float",
    "build_sentence_segments",
    "has_model_weights",
    "normalize_alignment_items",
    "validate_alignment_contract",
    "transcribe_with_qwen3",
]


def has_model_weights(model_dir: Path) -> bool:
    return (model_dir / "model.safetensors").exists()


def _qwen_info(language: str, word_timestamps: bool) -> dict[str, Any]:
    return {
        "language": language,
        "model": path_to_posix(QWEN3_ASR_MODEL_DIR),
        "forced_aligner": path_to_posix(QWEN3_ALIGNER_MODEL_DIR),
        "device": QWEN3_DEVICE_MAP,
        "compute_type": QWEN3_DTYPE,
        "batch_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
        "max_new_tokens": QWEN3_MAX_NEW_TOKENS,
        "word_timestamps": word_timestamps,
    }


def _qwen_load_merged(
    path: Path, plan: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    return _load_merged(path, plan, _qwen_info)


def _load_qwen_model() -> Any:
    try:
        import torch
        from qwen_asr import Qwen3ASRModel
        from transformers import GenerationConfig
    except ImportError as exc:
        raise RuntimeError(
            "Qwen3 ASR dependencies are not installed. Run "
            r"uv sync --python 3.12 --no-dev --extra qwen3, then "
            r"uv run --no-sync python -m scripts.setup.install_model --model qwen3."
        ) from exc
    if not torch.cuda.is_available():
        raise RuntimeError(
            "Qwen3 ASR requires an available CUDA GPU. Use the default whisper provider on CPU."
        )
    if not has_model_weights(QWEN3_ASR_MODEL_DIR) or not has_model_weights(
        QWEN3_ALIGNER_MODEL_DIR
    ):
        raise RuntimeError(
            "Qwen3 local models are missing. Run "
            r"uv run --no-sync python -m scripts.setup.install_model --model qwen3."
        )
    os.environ.setdefault("HF_ENDPOINT", DEFAULT_HF_ENDPOINT)
    asr_model = path_to_posix(QWEN3_ASR_MODEL_DIR)
    dtype = getattr(torch, QWEN3_DTYPE)
    generation_config = GenerationConfig.from_pretrained(asr_model, temperature=None)
    return Qwen3ASRModel.from_pretrained(
        asr_model,
        forced_aligner=path_to_posix(QWEN3_ALIGNER_MODEL_DIR),
        forced_aligner_kwargs={"dtype": dtype, "device_map": QWEN3_DEVICE_MAP},
        dtype=dtype,
        device_map=QWEN3_DEVICE_MAP,
        max_inference_batch_size=QWEN3_MAX_INFERENCE_BATCH_SIZE,
        max_new_tokens=QWEN3_MAX_NEW_TOKENS,
        generation_config=generation_config,
    )


def _qwen_result_payload(
    result: Any, plan: dict[str, Any], layout: dict[str, Any]
) -> dict[str, Any]:
    timestamp_data = getattr(result, "time_stamps", None)
    alignment = normalize_alignment_items(
        list(getattr(timestamp_data, "items", []) or [])
    )
    text = str(getattr(result, "text", "") or "").strip()
    validate_alignment_contract(
        text,
        alignment,
        (layout["end_sample"] - layout["start_sample"]) / SAMPLE_RATE,
        chunk_index=layout["index"],
        language=plan["request"]["language"],
    )
    return {
        "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
        "plan": plan,
        "chunk_index": layout["index"],
        "start_sample": layout["start_sample"],
        "end_sample": layout["end_sample"],
        "text": text,
        "word_timestamps": [asdict(item) for item in alignment],
    }


def transcribe_with_qwen3(
    audio_path: Path,
    language: str,
    workspace_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model_language = _qwen_language_name(language)
    paths = _qwen_workspace_paths(workspace_dir)
    cached_plan = _qwen_validate_plan(
        _qwen_load_json(paths["plan"]), audio_path, language
    )
    if cached_plan is not None:
        merged = _qwen_load_merged(paths["merged"], cached_plan)
        if merged is not None:
            terminal_info(
                logger,
                "[Transcribe] Qwen3 cache: complete; skipped audio decode, CUDA check, and model load",
            )
            return merged

    audio = decode_normalized_audio(audio_path)
    if (
        cached_plan is None
        or cached_plan["source"]["sample_count"] != audio.sample_count
    ):
        speech = detect_speech_samples(audio, DEFAULT_VAD_PARAMETERS)
        plan = _qwen_build_plan(audio_path, language, audio.sample_count, speech)
        ensure_dir(workspace_dir)
        write_json_atomic(paths["plan"], plan)
        write_json_atomic(
            paths["vad"],
            {
                "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
                "source": plan["source"],
                "parameters": asdict(DEFAULT_VAD_PARAMETERS),
                "speech_intervals": [
                    {"start_sample": start, "end_sample": end} for start, end in speech
                ],
            },
        )
    else:
        plan = cached_plan
    results = _qwen_load_chunk_results(workspace_dir, plan)
    pending = [
        item for item in plan["chunks"] if _qwen_chunk_key(item["index"]) not in results
    ]
    if not pending:
        text, alignment, segments = _qwen_merge(plan, results)
        write_json_atomic(
            paths["merged"],
            {
                "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
                "segmentation_version": QWEN3_SEGMENTATION_VERSION,
                "plan": plan,
                "text": text,
                "word_timestamps": [asdict(item) for item in alignment],
                "segments": segments,
            },
        )
        return _qwen_info(language, bool(alignment)), segments

    model = _load_qwen_model()
    failures: dict[str, str] = {}

    def cache(layout: dict[str, Any], result: Any) -> None:
        payload = _qwen_result_payload(result, plan, layout)
        key = _qwen_chunk_key(layout["index"])
        write_json_atomic(paths["results"] / f"{key}.json", payload)
        results[key] = payload
        write_json_atomic(paths["progress"], _qwen_progress(plan, results, failures))

    write_json_atomic(paths["progress"], _qwen_progress(plan, results))
    for offset in range(0, len(pending), QWEN3_MAX_INFERENCE_BATCH_SIZE):
        batch = pending[offset : offset + QWEN3_MAX_INFERENCE_BATCH_SIZE]
        inputs = [
            (audio.samples[item["start_sample"] : item["end_sample"]], SAMPLE_RATE)
            for item in batch
        ]
        try:
            batch_results = model.transcribe(
                inputs, language=model_language, return_time_stamps=True
            )
            if len(batch_results) != len(batch):
                raise RuntimeError("Qwen3 returned an unexpected batch result count.")
            for layout, result in zip(batch, batch_results, strict=True):
                cache(layout, result)
        except Exception:
            logger.warning("Qwen3 batch failed; isolating each chunk", exc_info=True)
            for layout, input_item in zip(batch, inputs, strict=True):
                key = _qwen_chunk_key(layout["index"])
                try:
                    isolated = model.transcribe(
                        [input_item], language=model_language, return_time_stamps=True
                    )
                    if len(isolated) != 1:
                        raise RuntimeError(
                            "Qwen3 returned an unexpected isolated result count."
                        )
                    cache(layout, isolated[0])
                except Exception as exc:
                    failures[key] = str(exc)
                    write_json_atomic(
                        paths["progress"], _qwen_progress(plan, results, failures)
                    )
    if failures:
        raise RuntimeError(
            f"Qwen3 chunks failed after isolation: {', '.join(sorted(failures))}"
        )
    text, alignment, segments = _qwen_merge(plan, results)
    write_json_atomic(
        paths["merged"],
        {
            "schema_version": QWEN3_CACHE_SCHEMA_VERSION,
            "segmentation_version": QWEN3_SEGMENTATION_VERSION,
            "plan": plan,
            "text": text,
            "word_timestamps": [asdict(item) for item in alignment],
            "segments": segments,
        },
    )
    return _qwen_info(language, bool(alignment)), segments
