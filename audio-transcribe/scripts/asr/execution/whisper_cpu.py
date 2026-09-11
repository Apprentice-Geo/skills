from __future__ import annotations

import math
import os
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable

from scripts.asr.chunking import (
    MAX_CHUNK_SAMPLES,
    MIN_CHUNK_SAMPLES,
    SAMPLE_RATE,
    ChunkLayout,
    NormalizedAudio,
    PlanningParameters,
    candidate_chunk_counts,
    plan_chunks,
)
from scripts.asr.pipeline_types import ChunkTranscript
from scripts.asr.providers.base import AsrProvider
from scripts.process_logging import get_logger
from scripts.runtime_options import TranscribeOptions

logger = get_logger(__name__)


class WhisperCpuPolicy:
    name = "whisper-cpu"

    def __init__(
        self, options: TranscribeOptions, logical_cpu_count: int | None = None
    ) -> None:
        self.options = options
        self.logical_cpu_count = logical_cpu_count
        maximum = (
            MAX_CHUNK_SAMPLES
            if options.max_chunk_seconds is None
            else round(float(options.max_chunk_seconds) * SAMPLE_RATE)
        )
        if maximum < MIN_CHUNK_SAMPLES:
            raise ValueError("max_chunk_seconds must be at least 30.")
        self.planning_parameters = PlanningParameters(MIN_CHUNK_SAMPLES, maximum)

    def execution_identity(self, sample_count: int) -> dict[str, Any]:
        budget = max(
            1, math.floor((self.logical_cpu_count or os.cpu_count() or 1) * 0.75)
        )
        requested_workers = self.options.num_workers
        requested_threads = self.options.cpu_threads
        for value, name in (
            (requested_workers, "num_workers"),
            (requested_threads, "cpu_threads"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(
                    f"Invalid {name}={value!r}: expected a positive integer."
                )

        def legal(workers: int) -> bool:
            return bool(
                candidate_chunk_counts(
                    sample_count,
                    group_size=workers,
                    count_strategy="divisible",
                    parameters=self.planning_parameters,
                )
            )

        if requested_workers is not None:
            threads = requested_threads or budget // requested_workers
            if threads < 1 or requested_workers * threads > budget:
                raise ValueError("Invalid worker configuration: exceeds CPU budget.")
            if not legal(requested_workers):
                raise ValueError(
                    f"No legal chunk count for num_workers={requested_workers}."
                )
            workers = requested_workers
        else:
            # 最小切片数 后续会得到保守的 worker 数
            # 但是对于最大化 worker 数会不会效率更优以及会不会导致更多硬切有待实验
            minimum_chunks = math.ceil(
                sample_count / self.planning_parameters.max_chunk_samples
            )
            workers = 0
            threads = 0
            # 从最大 worker 数开始尝试
            for candidate in range(min(budget, max(1, minimum_chunks)), 0, -1):
                if requested_threads is None and budget % candidate:
                    continue
                candidate_threads = requested_threads or budget // candidate
                if candidate * candidate_threads <= budget and legal(candidate):
                    workers, threads = candidate, candidate_threads
                    break
            if not workers:
                raise ValueError("No worker configuration can produce legal chunks.")
        return {
            "policy": self.name,
            "cpu_budget": budget,
            "num_workers": workers,
            "cpu_threads": threads,
            "count_strategy": "divisible",
            "group_size": workers,
            "retry_count": 1,
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
            count_strategy="divisible",
            parameters=self.planning_parameters,
        )

    def execute(
        self,
        provider: AsrProvider,
        audio: NormalizedAudio,
        pending: list[ChunkLayout],
        identity: dict[str, Any],
        cache: Callable[[ChunkTranscript], None],
        prepared_model: Any | None = None,
    ) -> dict[str, BaseException]:
        if not pending:
            return {}
        model = (
            prepared_model if prepared_model is not None else provider.prepare(identity)
        )
        attempts = {layout.index: 0 for layout in pending}
        current = list(pending)
        failures: dict[str, BaseException] = {}
        retry_count = int(identity.get("retry_count", 1))
        total_attempts = retry_count + 1
        with ThreadPoolExecutor(max_workers=int(identity["num_workers"])) as executor:
            while current:
                futures: dict[Future[ChunkTranscript], ChunkLayout] = {
                    executor.submit(
                        provider.transcribe_one, model, audio.slice(layout), layout
                    ): layout
                    for layout in current
                }
                retry: list[ChunkLayout] = []
                for future in as_completed(futures):
                    layout = futures[future]
                    try:
                        cache(future.result())
                    except Exception as exc:
                        attempt = attempts[layout.index] + 1
                        attempts[layout.index] = attempt
                        if attempt <= retry_count:
                            logger.warning(
                                "ASR chunk failed: provider=%s policy=%s "
                                "chunk=chunk_%03d attempt=%d/%d action=retry",
                                provider.name,
                                self.name,
                                layout.index,
                                attempt,
                                total_attempts,
                                exc_info=True,
                            )
                            retry.append(layout)
                        else:
                            logger.error(
                                "ASR chunk failed: provider=%s policy=%s "
                                "chunk=chunk_%03d attempt=%d/%d action=fail",
                                provider.name,
                                self.name,
                                layout.index,
                                attempt,
                                total_attempts,
                                exc_info=True,
                            )
                            failures[f"chunk_{layout.index:03d}"] = exc
                current = retry
        return failures
