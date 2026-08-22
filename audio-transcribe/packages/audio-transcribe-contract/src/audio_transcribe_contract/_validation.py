from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

from ._types import RawTimestamps, Transcript, _Provider

_SHA256_LENGTH = 64
_PROVIDERS = {"faster-whisper", "qwen3-asr"}
_ALIGNMENT_POLICY = {
    "schema_version": 1,
    "timestamp_resolution_ms": 1,
    "zero_duration": "drop_item_and_owned_text",
    "ordering": "strict",
}


class ResultValidationError(ValueError):
    """A transcription result is unsafe, inconsistent, or invalid."""


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError("Duplicate JSON key.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _read_object_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ResultValidationError(f"Invalid {label} JSON.") from exc
    if not isinstance(payload, dict):
        raise ResultValidationError(f"{label} must be a JSON object.")
    return payload


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise ResultValidationError(f"Invalid {label} JSON.") from exc
    return _read_object_bytes(data, label)


def _is_schema_one(value: Any) -> bool:
    return type(value) is int and value == 1


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResultValidationError(f"{field} must be a number.")
    try:
        parsed = float(value)
    except OverflowError as exc:
        raise ResultValidationError(
            f"{field} must be finite and non-negative."
        ) from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ResultValidationError(f"{field} must be finite and non-negative.")
    return parsed


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ResultValidationError(f"{field} must be a lowercase SHA-256 digest.")
    return value


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResultValidationError("request is not canonical JSON data.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ResultValidationError("Unable to read a public artifact.") from exc
    return digest.hexdigest()


def _artifact_path(root: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ResultValidationError(f"artifacts.{name} must be a relative path.")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ResultValidationError(f"artifacts.{name} must be a relative path.")
    try:
        resolved = (root / candidate).resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResultValidationError(
            f"artifacts.{name} escapes the result directory."
        ) from exc
    return resolved


def _validate_manifest(
    payload: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    str,
    str,
    _Provider,
    str,
    float,
]:
    if not _is_schema_one(payload.get("schema_version")):
        raise ResultValidationError("Invalid result manifest schema_version.")
    if payload.get("status") != "complete":
        raise ResultValidationError("Result manifest status must be complete.")

    audio = payload.get("audio")
    request = payload.get("request")
    artifacts = payload.get("artifacts")
    digests = payload.get("artifact_sha256")
    if (
        not isinstance(audio, dict)
        or not isinstance(request, dict)
        or not isinstance(artifacts, dict)
        or not isinstance(digests, dict)
    ):
        raise ResultValidationError("Invalid result manifest shape.")

    audio_id = _sha256(audio.get("id"), "audio.id")
    variant_id = _sha256(request.get("variant_id"), "request.variant_id")
    provider = request.get("provider")
    language = request.get("language")
    if not isinstance(provider, str) or provider not in _PROVIDERS:
        raise ResultValidationError("Invalid request.provider.")
    if not isinstance(language, str) or not language:
        raise ResultValidationError("request.language must be a non-empty string.")
    if request.get("alignment_policy") != _ALIGNMENT_POLICY:
        raise ResultValidationError("Invalid request.alignment_policy.")
    canonical_request = {
        key: value for key, value in request.items() if key != "variant_id"
    }
    if _canonical_sha256(canonical_request) != variant_id:
        raise ResultValidationError("request.variant_id does not match request.")
    for field in ("size", "sample_count", "sample_rate"):
        _finite_nonnegative(audio.get(field), f"audio.{field}")
    duration = _finite_nonnegative(audio.get("duration"), "audio.duration")
    return (
        artifacts,
        digests,
        audio_id,
        variant_id,
        cast(_Provider, provider),
        language,
        duration,
    )


def _validate_common_artifact(
    payload: dict[str, Any],
    *,
    name: str,
    audio_id: str,
    variant_id: str,
    provider: _Provider,
    language: str,
    duration: float,
) -> None:
    if not _is_schema_one(payload.get("schema_version")):
        raise ResultValidationError(f"Invalid {name} schema_version.")
    if payload.get("audio_id") != audio_id or payload.get("variant_id") != variant_id:
        raise ResultValidationError(f"{name} result identity does not match manifest.")
    if payload.get("provider") != provider:
        raise ResultValidationError(f"{name} provider does not match manifest.")
    if payload.get("language") != language:
        raise ResultValidationError(f"{name} language does not match manifest.")
    artifact_duration = _finite_nonnegative(payload.get("duration"), f"{name}.duration")
    if artifact_duration != duration:
        raise ResultValidationError(f"{name} duration does not match manifest.")


def _validate_transcript(payload: dict[str, Any], duration: float) -> Transcript:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ResultValidationError("transcript.segments must be a non-empty array.")
    previous_end = 0.0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or type(segment.get("id")) is not int:
            raise ResultValidationError("Invalid transcript segment identity.")
        if segment["id"] != index:
            raise ResultValidationError("Transcript segment ids must be continuous.")
        if not isinstance(segment.get("text"), str) or not segment["text"]:
            raise ResultValidationError("Transcript segment text must be non-empty.")
        start = _finite_nonnegative(segment.get("start"), "segment.start")
        end = _finite_nonnegative(segment.get("end"), "segment.end")
        if start < previous_end or end <= start or end > duration:
            raise ResultValidationError(
                "Transcript segment times must satisfy 0 <= start < end <= duration."
            )
        previous_end = end
    return cast(Transcript, payload)


def _validate_raw_timestamps(
    payload: dict[str, Any], duration: float, provider: _Provider
) -> RawTimestamps:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ResultValidationError("raw_timestamps.items must be a non-empty array.")
    previous_end = 0.0
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "text",
            "start",
            "end",
            "probability",
        }:
            raise ResultValidationError("Invalid raw timestamp item shape.")
        if not isinstance(item["text"], str) or not item["text"]:
            raise ResultValidationError("Raw timestamp text must be non-empty.")
        start = _finite_nonnegative(item["start"], "timestamp.start")
        end = _finite_nonnegative(item["end"], "timestamp.end")
        probability = item["probability"]
        if probability is not None:
            probability = _finite_nonnegative(probability, "timestamp.probability")
            if probability > 1:
                raise ResultValidationError(
                    "timestamp.probability must be between zero and one."
                )
        if start < previous_end or end <= start or end > duration:
            raise ResultValidationError(
                "Raw timestamp times must satisfy 0 <= start < end <= duration."
            )
        if provider == "qwen3-asr" and probability is not None:
            raise ResultValidationError(
                "Qwen3-ASR timestamp probability must remain null."
            )
        previous_end = end
    return cast(RawTimestamps, payload)
