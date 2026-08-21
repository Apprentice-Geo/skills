from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from scripts.asr.chunking import ChunkLayout
from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript


class AsrProvider(Protocol):
    name: str
    source: str
    language: str

    def request_identity(self) -> dict[str, Any]: ...

    def prepare(self, execution_identity: dict[str, Any]) -> Any: ...

    def transcribe_one(
        self, prepared: Any, samples: np.ndarray, layout: ChunkLayout
    ) -> ChunkTranscript: ...

    def final_info(
        self, plan: AsrPipelinePlan, words_present: bool
    ) -> dict[str, Any]: ...


class BatchAsrProvider(AsrProvider, Protocol):
    def transcribe_batch(
        self,
        prepared: Any,
        items: list[tuple[np.ndarray, ChunkLayout]],
    ) -> list[ChunkTranscript]: ...
