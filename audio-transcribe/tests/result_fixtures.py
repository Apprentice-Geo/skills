from typing import Any


def resolved_request(
    provider: str = "faster-whisper", language: str = "zh"
) -> dict[str, Any]:
    model = {"repo": "test/model", "revision": "a" * 40, "logical_id": "test-model"}
    identity: dict[str, Any] = {
        "provider": provider,
        "language": language,
        "model": model,
        "device": "cpu",
        "compute_type": "float32",
        "beam_size": 5,
        "word_timestamps": True,
    }
    execution: dict[str, Any] = {
        "policy": "whisper-cpu",
        "cpu_budget": 4,
        "num_workers": 1,
        "cpu_threads": 4,
        "count_strategy": "divisible",
        "group_size": 1,
        "retry_count": 1,
    }
    if provider == "qwen3-asr":
        model.update(
            aligner_repo="test/aligner",
            aligner_revision="b" * 40,
            aligner_logical_id="test-aligner",
        )
        identity = {
            "provider": provider,
            "language": language,
            "model": model,
            "device": "cuda:0",
            "compute_type": "bfloat16",
            "max_new_tokens": 4096,
            "model_language": "Chinese" if language == "zh" else "English",
            "return_time_stamps": True,
        }
        execution = {
            "policy": "qwen3-asr-cuda",
            "batch_size": 4,
            "group_size": 4,
            "count_strategy": "full",
            "batch_isolation": True,
        }
    return {
        "provider": provider,
        "language": language,
        "public_schema_version": 3,
        "provider_identity": identity,
        "execution_policy": execution,
        "alignment_policy": {
            "schema_version": 1,
            "timestamp_resolution_ms": 1,
            "zero_duration": "drop_item_and_owned_text",
            "ordering": "strict",
        },
        "vad_parameters": {
            "threshold": 0.35,
            "neg_threshold": 0.25,
            "min_speech_duration_ms": 0,
            "min_silence_duration_ms": 300,
            "max_speech_duration_s": None,
            "speech_pad_ms": 0,
            "sampling_rate": 16000,
        },
        "planning_parameters": {
            "min_chunk_samples": 480000,
            "max_chunk_samples": 2880000,
        },
        "segmentation_schema_version": 1,
        "text_normalization": {
            "schema_version": 1,
            "unicode_normalization": "NFKC",
            "zh_conversion": "OpenCC t2s",
        },
    }
