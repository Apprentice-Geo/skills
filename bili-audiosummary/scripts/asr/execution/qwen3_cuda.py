from __future__ import annotations

from typing import Any, Callable

from scripts.asr.chunking import (
    DEFAULT_PLANNING_PARAMETERS,
    ChunkLayout,
    NormalizedAudio,
    plan_chunks,
)
from scripts.asr.pipeline_types import ChunkTranscript
from scripts.config import QWEN3_MAX_INFERENCE_BATCH_SIZE


class Qwen3CudaPolicy:
    name = "qwen3-cuda"
    planning_parameters = DEFAULT_PLANNING_PARAMETERS

    def execution_identity(self, sample_count: int) -> dict[str, Any]:
        del sample_count
        return {
            "policy": self.name,
            "batch_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
            "count_strategy": "full",
            "group_size": QWEN3_MAX_INFERENCE_BATCH_SIZE,
            "batch_isolation": True,
        }

    def layouts(
        self,
        sample_count: int,
        speech_intervals: list[tuple[int, int]],
        identity: dict[str, Any],
    ) -> tuple[ChunkLayout, ...]:
        return plan_chunks(
            sample_count,
            speech_intervals,
            group_size=int(identity["group_size"]),
            count_strategy="full",
            parameters=self.planning_parameters,
        )

    def execute(
        self,
        provider: Any,
        audio: NormalizedAudio,
        pending: list[ChunkLayout],
        identity: dict[str, Any],
        cache: Callable[[ChunkTranscript], None],
    ) -> dict[str, str]:
        if not pending:
            return {}
        model = provider.prepare(identity)
        failures: dict[str, str] = {}
        batch_size = int(identity["batch_size"])
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            items = [(audio.slice(layout), layout) for layout in batch]
            try:
                for transcript in provider.transcribe_batch(model, items):
                    cache(transcript)
            except Exception:
                for samples, layout in items:
                    try:
                        cache(provider.transcribe_one(model, samples, layout))
                    except Exception as exc:
                        failures[f"chunk_{layout.index:03d}"] = str(exc)
        return failures
