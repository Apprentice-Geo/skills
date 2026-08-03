from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
JOB_FILENAME = "subtitle_job.json"
NORMALIZED_FILENAME = "normalized_transcript.json"
BEFORE_CORRECTION_FILENAME = "normalized_transcript.before_correction.json"
SUBTITLE_FILENAME = "subtitle.srt"
SKILL_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = SKILL_DIR / "results"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SubtitleJobError(ValueError):
    """Raised when an input or persisted subtitle job is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SubtitleJobError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def read_json_object(path: Path, *, decimal_numbers: bool = False) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=Decimal if decimal_numbers else float,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                SubtitleJobError(f"invalid JSON constant: {constant}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SubtitleJobError(f"cannot read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise SubtitleJobError(f"JSON root must be an object: {path}")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise SubtitleJobError(f"{field} must be a lowercase SHA-256")
    return value


def require_absolute_path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise SubtitleJobError(f"{field} must be an absolute path")
    return Path(value)


def require_job_artifact_path(value: object, field: str, job_dir: Path) -> Path:
    path = require_absolute_path(value, field)
    if not path.resolve().is_relative_to(job_dir.resolve()):
        raise SubtitleJobError(f"{field} escapes the job directory")
    return path


def compare_normalized_correction(
    baseline: dict[str, Any],
    normalized: dict[str, Any],
    *,
    audio_id: str,
    variant_id: str,
    manifest_path: Path,
) -> list[int]:
    for name, payload in (("before_correction", baseline), ("normalized_transcript", normalized)):
        if set(payload) != {
            "schema_version",
            "source",
            "provider",
            "language",
            "duration",
            "segments",
        }:
            raise SubtitleJobError(f"{name} has invalid fields")
        if (
            type(payload.get("schema_version")) is not int
            or payload["schema_version"] != SCHEMA_VERSION
        ):
            raise SubtitleJobError(f"{name} has an unsupported schema_version")
        source = payload.get("source")
        if not isinstance(source, dict) or source != {
            "manifest_path": str(manifest_path),
            "audio_id": audio_id,
            "variant_id": variant_id,
        }:
            raise SubtitleJobError(f"{name} source identity does not match the job")
        segments = payload.get("segments")
        if not isinstance(segments, list) or not segments:
            raise SubtitleJobError(f"{name} segments must be a non-empty array")
        if not isinstance(payload.get("provider"), str) or not payload["provider"]:
            raise SubtitleJobError(f"{name} provider must be a non-empty string")
        if not isinstance(payload.get("language"), str) or not payload["language"]:
            raise SubtitleJobError(f"{name} language must be a non-empty string")
        for index, segment in enumerate(segments):
            if (
                not isinstance(segment, dict)
                or set(segment) != {"id", "start", "end", "text"}
                or type(segment.get("id")) is not int
                or segment.get("id") != index
                or not isinstance(segment.get("text"), str)
                or not segment["text"]
            ):
                raise SubtitleJobError(f"{name} contains an invalid segment")

    if len(baseline["segments"]) != len(normalized["segments"]):
        raise SubtitleJobError("normalized transcript changed the segment count")
    if {key: value for key, value in baseline.items() if key != "segments"} != {
        key: value for key, value in normalized.items() if key != "segments"
    }:
        raise SubtitleJobError("normalized transcript changed metadata")
    for before, after in zip(baseline["segments"], normalized["segments"], strict=True):
        if {key: value for key, value in before.items() if key != "text"} != {
            key: value for key, value in after.items() if key != "text"
        }:
            raise SubtitleJobError("normalized transcript changed segment identity or timestamps")
    return [
        before["id"]
        for before, after in zip(baseline["segments"], normalized["segments"], strict=True)
        if before["text"] != after["text"]
    ]


def _decimal_number(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise SubtitleJobError(f"{field} must be a JSON number")
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise SubtitleJobError(f"{field} must be finite and non-negative")
    return number


def normalized_srt_segments(normalized: dict[str, Any]) -> list[tuple[int, int, str]]:
    duration = _decimal_number(normalized.get("duration"), "normalized_transcript.duration")
    segments = normalized["segments"]
    result: list[tuple[int, int, str]] = []
    previous_end = 0
    for segment in segments:
        segment_id = segment["id"]
        start = _decimal_number(segment["start"], f"segments[{segment_id}].start")
        end = _decimal_number(segment["end"], f"segments[{segment_id}].end")
        if end > duration:
            raise SubtitleJobError(f"segments[{segment_id}].end exceeds duration")
        start_ms = int((start * 1000).to_integral_value(rounding=ROUND_HALF_UP))
        end_ms = int((end * 1000).to_integral_value(rounding=ROUND_HALF_UP))
        if end_ms <= start_ms:
            raise SubtitleJobError(f"segments[{segment_id}] is empty after millisecond rounding")
        if start_ms < previous_end:
            raise SubtitleJobError(f"segments[{segment_id}] overlaps the previous segment")
        text = " ".join(segment["text"].split())
        if not text:
            raise SubtitleJobError(f"segments[{segment_id}].text is empty")
        result.append((start_ms, end_ms, text))
        previous_end = end_ms
    return result


def validate_job(job_path: Path, job: dict[str, Any]) -> None:
    if type(job.get("schema_version")) is not int or job["schema_version"] != SCHEMA_VERSION:
        raise SubtitleJobError("unsupported job schema_version")

    job_id = require_sha256(job.get("job_id"), "job_id")
    audio = job.get("audio")
    if not isinstance(audio, dict):
        raise SubtitleJobError("audio must be an object")
    if require_sha256(audio.get("id"), "audio.id") != job_id:
        raise SubtitleJobError("audio.id must match job_id")
    require_absolute_path(audio.get("path"), "audio.path")

    expected_path = (RESULTS_DIR / job_id / JOB_FILENAME).resolve()
    if job_path.resolve() != expected_path:
        raise SubtitleJobError("job path does not match job_id")

    changed_ids = job.get("changed_segment_ids")
    if (
        not isinstance(changed_ids, list)
        or any(type(item) is not int or item < 0 for item in changed_ids)
        or len(changed_ids) != len(set(changed_ids))
    ):
        raise SubtitleJobError("changed_segment_ids must contain unique non-negative integers")

    status = job.get("status")
    if status == "needs_transcription":
        if job.get("transcription") is not None:
            raise SubtitleJobError("needs_transcription job must not have transcription")
        if job.get("artifacts") is not None:
            raise SubtitleJobError("needs_transcription job must not have artifacts")
        if changed_ids:
            raise SubtitleJobError("needs_transcription job must not have changed segments")
        return
    if status != "editable":
        raise SubtitleJobError(f"unsupported job status: {status}")

    transcription = job.get("transcription")
    if not isinstance(transcription, dict):
        raise SubtitleJobError(f"{status} transcription must be an object")
    if set(transcription) != {"manifest_path", "variant_id"}:
        raise SubtitleJobError(f"{status} transcription has invalid fields")
    manifest_path = require_absolute_path(
        transcription.get("manifest_path"), "transcription.manifest_path"
    )
    variant_id = require_sha256(transcription.get("variant_id"), "transcription.variant_id")

    artifacts = job.get("artifacts")
    if not isinstance(artifacts, dict):
        raise SubtitleJobError(f"{status} artifacts must be an object")
    if set(artifacts) != {
        "normalized_transcript",
        "normalized_transcript_sha256",
        "before_correction",
        "before_correction_sha256",
        "subtitle",
        "subtitle_sha256",
    }:
        raise SubtitleJobError(f"{status} artifacts have invalid fields")
    job_dir = job_path.parent
    normalized_path = require_job_artifact_path(
        artifacts.get("normalized_transcript"), "artifacts.normalized_transcript", job_dir
    )
    baseline_path = require_job_artifact_path(
        artifacts.get("before_correction"), "artifacts.before_correction", job_dir
    )
    baseline_digest = require_sha256(
        artifacts.get("before_correction_sha256"), "artifacts.before_correction_sha256"
    )
    if not baseline_path.is_file() or sha256_file(baseline_path) != baseline_digest:
        raise SubtitleJobError("before-correction artifact digest mismatch")
    if not normalized_path.is_file():
        raise SubtitleJobError("normalized transcript artifact is missing")
    baseline = read_json_object(baseline_path, decimal_numbers=True)
    normalized = read_json_object(normalized_path, decimal_numbers=True)
    compare_normalized_correction(
        baseline,
        normalized,
        audio_id=job_id,
        variant_id=variant_id,
        manifest_path=manifest_path,
    )
    derived_values = (
        artifacts.get("normalized_transcript_sha256"),
        artifacts.get("subtitle"),
        artifacts.get("subtitle_sha256"),
    )
    if all(value is None for value in derived_values):
        if changed_ids:
            raise SubtitleJobError("unfinalized editable job must not have changed segments")
        return
    if any(value is None for value in derived_values):
        raise SubtitleJobError("editable derived artifact fields must all be set or null")
    require_sha256(
        artifacts["normalized_transcript_sha256"],
        "artifacts.normalized_transcript_sha256",
    )
    require_job_artifact_path(artifacts["subtitle"], "artifacts.subtitle", job_dir)
    require_sha256(artifacts["subtitle_sha256"], "artifacts.subtitle_sha256")
