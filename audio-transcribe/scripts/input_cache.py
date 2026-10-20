"""缓存可重建的音频解析信息；不认证公共转写结果。"""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from scripts.io_utils import canonical_sha256, read_json, write_json_atomic


def audio_preparation_identity() -> dict[str, Any]:
    return {
        "sample_rate": 16000,
        "channels": "mono",
        "sample_format": "float32",
        "dependencies": {name: version(name) for name in ("faster-whisper", "av")},
    }


def load_input(path: Path, identity: dict[str, Any]) -> tuple[int, str | None] | None:
    try:
        value = read_json(path)
        if not isinstance(value, dict) or set(value) != {
            "identity",
            "sample_count",
            "language",
            "digest",
        }:
            return None
        payload = {key: item for key, item in value.items() if key != "digest"}
        if (
            value["identity"] != identity
            or canonical_sha256(payload) != value["digest"]
        ):
            return None
        count, language = value["sample_count"], value["language"]
        if type(count) is not int or count <= 0:
            return None
        if identity["language_detection"] is None:
            if language is not None:
                return None
        elif (
            not isinstance(language, str)
            or not language
            or language != language.strip().lower()
        ):
            return None
        return count, language
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


def save_input(
    path: Path, identity: dict[str, Any], count: int, language: str | None
) -> None:
    payload = {"identity": identity, "sample_count": count, "language": language}
    write_json_atomic(path, {**payload, "digest": canonical_sha256(payload)})
