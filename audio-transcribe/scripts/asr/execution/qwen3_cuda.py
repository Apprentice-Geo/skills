from __future__ import annotations

from typing import Any, Callable

from scripts.asr.chunking import (
    DEFAULT_PLANNING_PARAMETERS,
    ChunkLayout,
    NormalizedAudio,
    plan_chunks,
)
from scripts.asr.pipeline_types import ChunkTranscript
from scripts.asr.providers.base import BatchAsrProvider
from scripts.config import QWEN3_MAX_INFERENCE_BATCH_SIZE
from scripts.process_logging import get_logger

logger = get_logger(__name__)


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
        provider: BatchAsrProvider,
        audio: NormalizedAudio,
        pending: list[ChunkLayout],
        identity: dict[str, Any],
        cache: Callable[[ChunkTranscript], None],
    ) -> dict[str, BaseException]:
        if not pending:
            return {}
        model = provider.prepare(identity)
        failures: dict[str, BaseException] = {}
        batch_size = int(identity["batch_size"])
        for offset in range(0, len(pending), batch_size):
            batch_number = offset // batch_size + 1
            batch = pending[offset : offset + batch_size]
            items = [(audio.slice(layout), layout) for layout in batch]
            batch_failed = False
            try:
                for transcript in provider.transcribe_batch(model, items):
                    cache(transcript)
            except Exception:
                batch_failed = True
                logger.warning(
                    "ASR batch failed: provider=%s policy=%s batch=%d "
                    "chunks=chunk_%03d..chunk_%03d attempt=batch action=isolate",
                    provider.name,
                    self.name,
                    batch_number,
                    batch[0].index,
                    batch[-1].index,
                    exc_info=True,
                )
            if not batch_failed:
                continue
            for samples, layout in items:
                try:
                    cache(provider.transcribe_one(model, samples, layout))
                except Exception as exc:
                    logger.error(
                        "ASR chunk failed: provider=%s policy=%s batch=%d "
                        "chunk=chunk_%03d attempt=isolation action=fail",
                        provider.name,
                        self.name,
                        batch_number,
                        layout.index,
                        exc_info=True,
                    )
                    failures[f"chunk_{layout.index:03d}"] = exc
        return failures
