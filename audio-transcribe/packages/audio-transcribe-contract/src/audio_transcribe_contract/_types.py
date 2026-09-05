from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type _JsonNumber = int | float
type _Provider = Literal["faster-whisper", "qwen3-asr"]
PUBLIC_SCHEMA_VERSION = 2


class _Audio(TypedDict):
    id: str
    size: _JsonNumber
    sample_count: _JsonNumber
    sample_rate: _JsonNumber
    duration: _JsonNumber


class _AlignmentPolicy(TypedDict):
    schema_version: Literal[1]
    timestamp_resolution_ms: Literal[1]
    zero_duration: Literal["drop_item_and_owned_text"]
    ordering: Literal["strict"]


class _Request(TypedDict):
    config_digest: str
    provider: _Provider
    language: str
    alignment_policy: _AlignmentPolicy
    public_schema_version: Literal[2]
    provider_identity: NotRequired[JsonValue]
    execution_policy: NotRequired[JsonValue]
    vad_parameters: NotRequired[JsonValue]
    planning_parameters: NotRequired[JsonValue]
    segmentation_schema_version: NotRequired[JsonValue]
    text_normalization: NotRequired[JsonValue]


class _Artifacts(TypedDict):
    transcript: str


class _ArtifactDigests(TypedDict):
    transcript: str


class ResultManifest(TypedDict):
    schema_version: Literal[2]
    status: Literal["complete"]
    audio: _Audio
    request: _Request
    artifacts: _Artifacts
    artifact_sha256: _ArtifactDigests


class _PublicArtifact(TypedDict):
    schema_version: Literal[2]
    audio_id: str
    config_digest: str
    provider: _Provider
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
