from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type _JsonNumber = int | float
type _Provider = Literal["faster-whisper", "qwen3-asr"]
PUBLIC_SCHEMA_VERSION = 3


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


class _Model(TypedDict):
    repo: str
    revision: str
    logical_id: str


class _QwenModel(_Model):
    aligner_repo: str
    aligner_revision: str
    aligner_logical_id: str


class _WhisperIdentity(TypedDict):
    provider: Literal["faster-whisper"]
    language: str
    model: _Model
    beam_size: int
    device: Literal["cpu"]
    compute_type: str
    word_timestamps: Literal[True]


class _QwenIdentity(TypedDict):
    provider: Literal["qwen3-asr"]
    language: str
    model_language: str
    model: _QwenModel
    device: str
    compute_type: str
    max_new_tokens: int
    return_time_stamps: Literal[True]


class _WhisperExecution(TypedDict):
    policy: Literal["whisper-cpu"]
    cpu_budget: int
    num_workers: int
    cpu_threads: int
    count_strategy: Literal["divisible"]
    group_size: int
    retry_count: int


class _QwenExecution(TypedDict):
    policy: Literal["qwen3-asr-cuda"]
    batch_size: int
    count_strategy: Literal["full"]
    group_size: int
    batch_isolation: Literal[True]


class _VadParameters(TypedDict):
    threshold: _JsonNumber
    neg_threshold: _JsonNumber
    min_speech_duration_ms: int
    min_silence_duration_ms: int
    max_speech_duration_s: _JsonNumber | None
    speech_pad_ms: int
    sampling_rate: int


class _PlanningParameters(TypedDict):
    min_chunk_samples: int
    max_chunk_samples: int


class _TextNormalization(TypedDict):
    schema_version: Literal[1]
    unicode_normalization: Literal["NFKC"]
    zh_conversion: Literal["OpenCC t2s"]


class _Request(TypedDict):
    config_digest: str
    provider: _Provider
    language: str
    alignment_policy: _AlignmentPolicy
    public_schema_version: Literal[3]
    provider_identity: _WhisperIdentity | _QwenIdentity
    execution_policy: _WhisperExecution | _QwenExecution
    vad_parameters: _VadParameters
    planning_parameters: _PlanningParameters
    segmentation_schema_version: Literal[1]
    text_normalization: _TextNormalization


class _Artifacts(TypedDict):
    transcript: str


class _ArtifactDigests(TypedDict):
    transcript: str


class ResultManifest(TypedDict):
    schema_version: Literal[3]
    status: Literal["complete"]
    audio: _Audio
    request: _Request
    artifacts: _Artifacts
    artifact_sha256: _ArtifactDigests


class _PublicArtifact(TypedDict):
    schema_version: Literal[3]
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
