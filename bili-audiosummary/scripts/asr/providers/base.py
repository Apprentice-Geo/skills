from __future__ import annotations

from typing import Any, Protocol

from scripts.asr.pipeline_types import AsrPipelinePlan, ChunkTranscript


class AsrProvider(Protocol):
    name: str
    source: str
    language: str

    def request_identity(self) -> dict[str, Any]: ...

    def prepare(self, execution_identity: dict[str, Any]) -> Any: ...

    def transcribe_one(
        self, prepared: Any, samples: Any, layout: Any
    ) -> ChunkTranscript: ...

    def final_info(
        self, plan: AsrPipelinePlan, words_present: bool
    ) -> dict[str, Any]: ...

    def postprocess_segments(
        self, segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...
