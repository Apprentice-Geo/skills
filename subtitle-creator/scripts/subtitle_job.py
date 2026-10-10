from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.process_logging import get_logger, warning
from scripts.runtime_paths import RuntimePaths

SCHEMA_VERSION = 2
JOB_SCHEMA_VERSION = 3
JOB_FILENAME = "subtitle_job.json"
NORMALIZED_FILENAME = "normalized_transcript.json"
BEFORE_CORRECTION_FILENAME = "normalized_transcript.before_correction.json"
SUBTITLE_FILENAME = "subtitle.srt"
SKILL_DIR = Path(__file__).resolve().parents[1]
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
JOB_KEYS = {
    "schema_version",
    "job_id",
    "status",
    "audio",
    "artifacts",
    "changed_segment_ids",
}


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


def load_job(path: Path) -> dict[str, Any]:
    job = read_json_object(path)
    if type(job.get("schema_version")) is int and job["schema_version"] == 2:
        old_status = job.get("status")
        statuses = {
            "needs_transcription": "transcription_unbound",
            "editable": "transcription_bound",
        }
        if not isinstance(old_status, str) or old_status not in statuses:
            raise SubtitleJobError(f"unsupported legacy job status: {old_status}")
        job["schema_version"] = JOB_SCHEMA_VERSION
        job["status"] = statuses[old_status]
    return job


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
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def transcript_json(value: Any) -> str:
    """序列化已验证的 JSON，保留 Decimal 的数值精度。"""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise SubtitleJobError("JSON numbers must be finite")
        return str(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + transcript_json(item)
                for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(transcript_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


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
        if payload.get("source") != "audio_transcribe":
            raise SubtitleJobError(f"{name} source is invalid")
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


def _timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def expected_srt_bytes(normalized: dict[str, Any]) -> bytes:
    blocks = [
        f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}"
        for index, (start, end, text) in enumerate(normalized_srt_segments(normalized), start=1)
    ]
    return ("\n\n".join(blocks) + "\n").encode("utf-8-sig")


def subtitle_matches_normalized(subtitle_path: Path, normalized: dict[str, Any]) -> bool:
    return subtitle_path.is_file() and subtitle_path.read_bytes() == expected_srt_bytes(normalized)


def validate_job_identity(
    job_path: Path,
    job: dict[str, Any],
    *,
    results_dir: Path | None = None,
) -> None:
    if set(job) != JOB_KEYS:
        raise SubtitleJobError("subtitle job has an invalid top-level shape")
    if type(job.get("schema_version")) is not int or job["schema_version"] != JOB_SCHEMA_VERSION:
        raise SubtitleJobError("unsupported job schema_version")

    job_id = require_sha256(job.get("job_id"), "job_id")
    audio = job.get("audio")
    if not isinstance(audio, dict):
        raise SubtitleJobError("audio must be an object")
    if require_sha256(audio.get("id"), "audio.id") != job_id:
        raise SubtitleJobError("audio.id must match job_id")
    require_absolute_path(audio.get("path"), "audio.path")

    results_dir = results_dir or RuntimePaths.resolve().results_dir
    expected_path = (results_dir / job_id / JOB_FILENAME).resolve()
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
    if status == "transcription_unbound":
        if job.get("artifacts") is not None:
            raise SubtitleJobError("transcription_unbound job must not have artifacts")
        if changed_ids:
            raise SubtitleJobError("transcription_unbound job must not have changed segments")
        return
    if status != "transcription_bound":
        raise SubtitleJobError(f"unsupported job status: {status}")

    artifacts = job.get("artifacts")
    if not isinstance(artifacts, dict):
        raise SubtitleJobError(f"{status} artifacts must be an object")
    if set(artifacts) != {
        "normalized_transcript",
        "before_correction",
        "before_correction_sha256",
        "subtitle",
    }:
        raise SubtitleJobError(f"{status} artifacts have invalid fields")
    for field in ("normalized_transcript", "before_correction"):
        require_job_artifact_path(artifacts[field], f"artifacts.{field}", job_path.parent)
    require_sha256(artifacts["before_correction_sha256"], "artifacts.before_correction_sha256")
    if artifacts["subtitle"] is not None:
        require_job_artifact_path(artifacts["subtitle"], "artifacts.subtitle", job_path.parent)


def validate_job(
    job_path: Path,
    job: dict[str, Any],
    *,
    results_dir: Path | None = None,
    allow_stale_derived: bool = False,
) -> None:
    validate_job_identity(job_path, job, results_dir=results_dir)
    if job["status"] == "transcription_unbound":
        return
    artifacts = job["artifacts"]
    changed_ids = job["changed_segment_ids"]
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
    expected_changed_ids = compare_normalized_correction(baseline, normalized)
    normalized_srt_segments(baseline)
    normalized_srt_segments(normalized)
    if changed_ids != expected_changed_ids and not allow_stale_derived:
        raise SubtitleJobError("changed_segment_ids does not match normalized transcript")
    recorded_subtitle = artifacts.get("subtitle")
    if recorded_subtitle is None:
        return
    subtitle_path = require_job_artifact_path(recorded_subtitle, "artifacts.subtitle", job_dir)
    if not subtitle_path.is_file() and not allow_stale_derived:
        raise SubtitleJobError("subtitle artifact is missing")
    if (
        subtitle_path.is_file()
        and not subtitle_matches_normalized(subtitle_path, normalized)
        and not allow_stale_derived
    ):
        raise SubtitleJobError(
            "subtitle artifact timeline or text does not match normalized transcript"
        )


def publish_transcript(
    job_path: Path,
    job: dict[str, Any],
    baseline_bytes: bytes,
    *,
    normalized_bytes: bytes | None = None,
    results_dir: Path | None = None,
) -> Path:
    validate_job_identity(job_path, job, results_dir=results_dir)
    old_artifacts = job["artifacts"]
    old_paths = (
        [Path(old_artifacts[field]) for field in ("before_correction", "normalized_transcript")]
        if old_artifacts is not None
        else []
    )
    # 新文件先写入独立目录；原子切换 job 前，旧绑定和校正始终可用。
    snapshot_dir = _create_snapshot_directory(job_path.parent)
    baseline_path = snapshot_dir / BEFORE_CORRECTION_FILENAME
    normalized_path = snapshot_dir / NORMALIZED_FILENAME
    try:
        for path, content in (
            (baseline_path, baseline_bytes),
            (normalized_path, baseline_bytes if normalized_bytes is None else normalized_bytes),
        ):
            with path.open("wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
        new_job = {
            **job,
            "status": "transcription_bound",
            "changed_segment_ids": compare_normalized_correction(
                read_json_object(baseline_path, decimal_numbers=True),
                read_json_object(normalized_path, decimal_numbers=True),
            ),
            "artifacts": {
                "normalized_transcript": str(normalized_path),
                "before_correction": str(baseline_path),
                "before_correction_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                "subtitle": None,
            },
        }
        validate_job(job_path, new_job, results_dir=results_dir)
    except BaseException:
        # 清理失败只记录警告，保留原始发布异常。
        _cleanup_transcript_files(job_path.parent, [baseline_path, normalized_path])
        raise
    try:
        atomic_write_json(job_path, new_job)
    except BaseException:
        # 原子替换后仍可能抛异常；以磁盘声明确认引用，不能凭异常判断未提交。
        try:
            persisted_job = load_job(job_path)
            validate_job_identity(job_path, persisted_job, results_dir=results_dir)
        except (OSError, ValueError, TypeError) as error:
            warning(
                get_logger(__name__),
                "cannot confirm transcript publication for %s; keeping both snapshots: %s",
                job_path,
                error,
            )
        else:
            if persisted_job == new_job:
                _cleanup_transcript_files(job_path.parent, old_paths)
            elif persisted_job == job:
                _cleanup_transcript_files(job_path.parent, [baseline_path, normalized_path])
            else:
                warning(
                    get_logger(__name__),
                    "cannot confirm transcript publication for %s; keeping both snapshots",
                    job_path,
                )
        raise
    # job 已提交；旧快照清理失败不能回滚或删除当前快照。
    _cleanup_transcript_files(job_path.parent, old_paths)
    return normalized_path


def _cleanup_transcript_files(job_dir: Path, paths: list[Path]) -> None:
    directories: set[Path] = set()
    for path in paths:
        try:
            directory = path.parent
            # 只删除本任务直接子目录内的受管文件，不递归删除用户附加内容。
            if (
                path.name not in {BEFORE_CORRECTION_FILENAME, NORMALIZED_FILENAME}
                or re.fullmatch(r"transcript-[0-9a-f]{32}", directory.name) is None
                or directory.parent != job_dir.resolve()
                or directory.is_symlink()
                or directory.is_junction()
                or directory.resolve().parent != job_dir.resolve()
                or path.is_symlink()
                or path.is_junction()
            ):
                continue
            path.unlink(missing_ok=True)
            directories.add(directory)
        except OSError as error:
            warning(get_logger(__name__), "cannot clean transcript artifact %s: %s", path, error)
    for directory in directories:
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            warning(
                get_logger(__name__), "cannot clean transcript directory %s: %s", directory, error
            )


def _create_snapshot_directory(parent: Path) -> Path:
    for _ in range(10):
        path = parent / f"transcript-{uuid4().hex}"
        try:
            # Windows 继承父目录 ACL，避免 0o700 排除沙箱身份；POSIX 保持私有权限。
            path.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        except FileExistsError:
            continue
        return path.resolve()
    raise FileExistsError(f"cannot create a unique transcript directory under {parent}")
