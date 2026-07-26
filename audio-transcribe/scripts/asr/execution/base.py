from __future__ import annotations

from typing import Any, Callable, Protocol, TypeVar

from scripts.asr.chunking import ChunkLayout, NormalizedAudio, PlanningParameters
from scripts.asr.pipeline_types import ChunkTranscript
from scripts.asr.providers.base import AsrProvider

ProviderT = TypeVar(
    "ProviderT",
    bound=AsrProvider,
    contravariant=True,
)


class ExecutionPolicy(Protocol[ProviderT]):
    name: str
    planning_parameters: PlanningParameters

    def execution_identity(self, sample_count: int) -> dict[str, Any]: ...

    def layouts(
        self,
        sample_count: int,
        speech_intervals: list[tuple[int, int]],
        identity: dict[str, Any],
    ) -> tuple[ChunkLayout, ...]: ...

    def execute(
        self,
        provider: ProviderT,
        audio: NormalizedAudio,
        pending: list[ChunkLayout],
        identity: dict[str, Any],
        cache: Callable[[ChunkTranscript], None],
    ) -> dict[str, BaseException]: ...
