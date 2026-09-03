from __future__ import annotations

import hashlib
import json
import re
import wave
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from scripts.io_utils import sha256_file

LANGUAGES = ("zh", "en")
MINUTES = (8, 16, 32, 64)
REFERENCE_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_object(value: Any, name: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    actual = set(value)
    missing = fields - actual
    unknown = actual - fields
    if missing:
        raise ValueError(f"{name} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")
    return value


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA256 digest")
    return value


def _read_utf8_lf(path: Path, name: str) -> tuple[bytes, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Unable to read {name}: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{name} must not contain a UTF-8 BOM")
    if b"\r" in raw:
        raise ValueError(f"{name} must use LF line endings")
    if b"\0" in raw:
        raise ValueError(f"{name} must not contain NUL")
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8") from exc


def _manifest_path(root: Path, value: Any, name: str) -> tuple[str, Path]:
    path_text = _require_string(value, name)
    logical = PurePosixPath(path_text)
    if (
        logical.is_absolute()
        or "\\" in path_text
        or any(part in ("", ".", "..") for part in logical.parts)
    ):
        raise ValueError(f"{name} must be a safe relative POSIX path")
    candidate = root.joinpath(*logical.parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{name} escapes the reference root") from exc
    return path_text, candidate


def load_reference_manifest(path: Path) -> dict[str, Any]:
    """Validate reference manifest metadata without opening parts or sample WAVs."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Reference manifest must be a regular file: {path}")
    raw, text = _read_utf8_lf(path, "reference manifest")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Reference manifest is not valid JSON") from exc
    manifest = _require_object(
        value,
        "reference manifest",
        {"schema_version", "languages"},
    )
    if manifest["schema_version"] != REFERENCE_SCHEMA_VERSION:
        raise ValueError("Unsupported reference manifest schema")

    languages = _require_object(
        manifest["languages"], "reference languages", set(LANGUAGES)
    )
    paths: set[str] = set()
    resolved_paths: set[Path] = set()
    root = path.parent
    normalized_languages: dict[str, Any] = {}
    for language in LANGUAGES:
        entry = _require_object(
            languages[language],
            f"reference language {language}",
            {"source", "parts"},
        )
        source = _require_object(
            entry["source"],
            f"reference source {language}",
            {"work", "author", "reader", "audio_url", "text_url"},
        )
        normalized_source = {
            field: _require_string(
                source[field], f"reference source {language}.{field}"
            )
            for field in ("work", "author", "reader", "audio_url", "text_url")
        }
        parts = entry["parts"]
        if not isinstance(parts, list) or len(parts) != len(MINUTES):
            raise ValueError(f"reference parts for {language} must contain four items")
        normalized_parts = []
        through_minutes = []
        for index, raw_part in enumerate(parts):
            part = _require_object(
                raw_part,
                f"reference part {language}[{index}]",
                {"through_minutes", "path", "sha256", "sample_audio_sha256"},
            )
            minute = part["through_minutes"]
            if not isinstance(minute, int) or isinstance(minute, bool):
                raise ValueError("reference through_minutes must be an integer")
            through_minutes.append(minute)
            path_text, resolved = _manifest_path(
                root, part["path"], f"reference part {language}[{index}].path"
            )
            if path_text in paths:
                raise ValueError(f"reference part path is duplicated: {path_text}")
            paths.add(path_text)
            resolved_identity = resolved.resolve(strict=False)
            if resolved_identity in resolved_paths:
                raise ValueError(f"reference part path is duplicated: {path_text}")
            resolved_paths.add(resolved_identity)
            normalized_parts.append(
                {
                    "through_minutes": minute,
                    "path": path_text,
                    "resolved_path": resolved,
                    "sha256": _require_sha256(
                        part["sha256"], f"reference part {language}[{index}].sha256"
                    ),
                    "sample_audio_sha256": _require_sha256(
                        part["sample_audio_sha256"],
                        f"reference part {language}[{index}].sample_audio_sha256",
                    ),
                }
            )
        if through_minutes != list(MINUTES):
            raise ValueError(
                f"reference through_minutes for {language} must be {list(MINUTES)}"
            )
        normalized_languages[language] = {
            "source": normalized_source,
            "parts": normalized_parts,
        }
    return {
        "schema_version": manifest["schema_version"],
        "languages": normalized_languages,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "root": root,
    }


def _samples_digests(path: Path) -> dict[tuple[str, int], str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read benchmark samples metadata: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
        raise ValueError("Benchmark samples metadata is invalid")
    result: dict[tuple[str, int], str] = {}
    for source in value["sources"]:
        if not isinstance(source, dict):
            raise ValueError("Benchmark sample source is invalid")
        language = source.get("language")
        cuts = source.get("cuts")
        if language not in LANGUAGES or not isinstance(cuts, list):
            raise ValueError("Benchmark sample source is invalid")
        for cut in cuts:
            if not isinstance(cut, dict):
                raise ValueError("Benchmark sample cut is invalid")
            minute = cut.get("minutes")
            digest = _require_sha256(cut.get("sha256"), "benchmark sample sha256")
            if not isinstance(minute, int) or isinstance(minute, bool):
                raise ValueError("Benchmark sample identity is invalid")
            key = (language, minute)
            if minute not in MINUTES or key in result:
                raise ValueError("Benchmark sample identity is invalid")
            result[key] = digest
    expected = {(language, minute) for language in LANGUAGES for minute in MINUTES}
    if set(result) != expected:
        raise ValueError("Benchmark samples metadata must describe all eight samples")
    return result


def _reject_symlink(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Reference part must not be a symbolic link: {path}")


def _validate_sample_wav(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as stream:
            if (
                stream.getnchannels() != 1
                or stream.getsampwidth() != 2
                or stream.getframerate() != 16_000
                or stream.getnframes() == 0
            ):
                raise ValueError(
                    f"Benchmark sample must be mono 16 kHz PCM WAV: {path}"
                )
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"Benchmark sample is not a readable PCM WAV: {path}") from exc


def load_reference_samples(
    manifest: dict[str, Any],
    samples: Iterable[tuple[str, int]],
    *,
    data_dir: Path,
    samples_manifest_path: Path,
    normalize_units: Callable[[str, str], list[str]],
    unit_digest: Callable[[list[str]], str],
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load and verify only the cumulative references and WAVs requested."""
    items = list(samples)
    if not items or any(
        not isinstance(item, tuple)
        or len(item) != 2
        or item[0] not in LANGUAGES
        or not isinstance(item[1], int)
        or isinstance(item[1], bool)
        or item[1] not in MINUTES
        for item in items
    ):
        raise ValueError("Reference sample selection is invalid")
    requested = set(items)
    sample_digests = _samples_digests(samples_manifest_path)
    root = manifest["root"].resolve(strict=True)
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for language in LANGUAGES:
        language_minutes = sorted(
            minute for item_language, minute in requested if item_language == language
        )
        if not language_minutes:
            continue
        maximum = max(language_minutes)
        cumulative = ""
        for part in manifest["languages"][language]["parts"]:
            minute = part["through_minutes"]
            if minute > maximum:
                break
            path = part["resolved_path"]
            _reject_symlink(path, root)
            if not path.is_file():
                raise ValueError(f"Reference part must be a regular file: {path}")
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"Reference part escapes the reference root: {path}"
                ) from exc
            raw, text = _read_utf8_lf(path, "reference part")
            if hashlib.sha256(raw).hexdigest() != part["sha256"]:
                raise ValueError(f"Reference part SHA256 mismatch: {path}")
            cumulative += text
            if minute not in language_minutes:
                continue
            units = normalize_units(cumulative, language)
            if not units:
                raise ValueError(
                    f"Reference text is empty after normalization: {language}-{minute}"
                )
            expected_audio = part["sample_audio_sha256"]
            if sample_digests[(language, minute)] != expected_audio:
                raise ValueError(
                    f"Reference audio SHA256 does not match samples.json: {language}-{minute}"
                )
            audio = data_dir / f"{language}-{minute}min.wav"
            if not audio.is_file() or sha256_file(audio) != expected_audio:
                raise ValueError(f"Benchmark sample SHA256 mismatch: {audio}")
            _validate_sample_wav(audio)
            result[(language, minute)] = {
                "language": language,
                "minutes": minute,
                "text": cumulative,
                "units": units,
                "audio_sha256": expected_audio,
                "reference_sha256": unit_digest(units),
            }
    if set(result) != requested:
        raise ValueError("Unable to load all requested reference samples")
    return result


def freeze_reference_set(
    manifest: dict[str, Any], samples: dict[tuple[str, int], dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "manifest_sha256": manifest["manifest_sha256"],
        "samples": [
            {
                "language": item["language"],
                "minutes": item["minutes"],
                "audio_sha256": item["audio_sha256"],
                "reference_sha256": item["reference_sha256"],
            }
            for _, item in sorted(samples.items())
        ],
    }
