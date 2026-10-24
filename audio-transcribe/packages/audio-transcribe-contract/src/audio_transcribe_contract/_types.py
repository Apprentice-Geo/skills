from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type _JsonNumber = int | float
PUBLIC_SCHEMA_VERSION = 4


class _Audio(TypedDict):
    id: str
    size: _JsonNumber
    sample_count: _JsonNumber
    sample_rate: _JsonNumber
    duration: _JsonNumber


class _Request(TypedDict):
    config_digest: str
    provider: str
    language: str
    alignment_policy: dict[str, JsonValue]
    provider_identity: dict[str, JsonValue]
    execution_policy: dict[str, JsonValue]
    vad_parameters: dict[str, JsonValue]
    planning_parameters: dict[str, JsonValue]
    text_normalization: dict[str, JsonValue]


class _Artifacts(TypedDict):
    transcript: str


class _ArtifactDigests(TypedDict):
    transcript: str


class ResultManifest(TypedDict):
    schema_version: Literal[4]
    status: Literal["complete"]
    audio: _Audio
    request: _Request
    artifacts: _Artifacts
    artifact_sha256: _ArtifactDigests


class _PublicArtifact(TypedDict):
    schema_version: Literal[4]
    audio_id: str
    config_digest: str
    provider: str
    language: str
    duration: _JsonNumber


class _TranscriptSegment(TypedDict):
    id: int
    start: _JsonNumber
    end: _JsonNumber
    text: str


class _AlignmentItem(TypedDict):
    text: str
    start: _JsonNumber
    end: _JsonNumber
    probability: _JsonNumber | None


class Transcript(_PublicArtifact):
    segments: list[_TranscriptSegment]
    items: list[_AlignmentItem]


@dataclass(frozen=True)
class TranscriptionResult:
    manifest_path: Path
    transcript_path: Path
    manifest: ResultManifest
    transcript: Transcript
