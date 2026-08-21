from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type _JsonNumber = int | float
type _Provider = Literal["faster-whisper", "qwen3-asr"]


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
    variant_id: str
    provider: _Provider
    language: str
    alignment_policy: _AlignmentPolicy
    provider_identity: NotRequired[JsonValue]
    execution_policy: NotRequired[JsonValue]
    vad_parameters: NotRequired[JsonValue]
    planning_parameters: NotRequired[JsonValue]
    segmentation_schema_version: NotRequired[JsonValue]
    text_normalization: NotRequired[JsonValue]


class _Artifacts(TypedDict):
    transcript: str
    raw_timestamps: str
    log: str
    workspace: str


class _ArtifactDigests(TypedDict):
    transcript: str
    raw_timestamps: str


class ResultManifest(TypedDict):
    schema_version: Literal[1]
    status: Literal["complete"]
    audio: _Audio
    request: _Request
    artifacts: _Artifacts
    artifact_sha256: _ArtifactDigests


class _PublicArtifact(TypedDict):
    schema_version: Literal[1]
    audio_id: str
    variant_id: str
    provider: _Provider
    language: str
    duration: _JsonNumber


class _TranscriptSegment(TypedDict):
    id: int
    start: _JsonNumber
    end: _JsonNumber
    text: str


class Transcript(_PublicArtifact):
    segments: list[_TranscriptSegment]


class _RawTimestamp(TypedDict):
    text: str
    start: _JsonNumber
    end: _JsonNumber
    probability: _JsonNumber | None


class RawTimestamps(_PublicArtifact):
    items: list[_RawTimestamp]


@dataclass(frozen=True)
class TranscriptionResult:
    manifest_path: Path
    transcript_path: Path
    raw_timestamps_path: Path
    manifest: ResultManifest
    transcript: Transcript
    raw_timestamps: RawTimestamps
