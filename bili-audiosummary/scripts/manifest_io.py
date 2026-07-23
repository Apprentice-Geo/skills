from pathlib import Path
from typing import Any

from scripts.config import SKILL_ROOT
from scripts.utils import path_to_posix, read_json


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return SKILL_ROOT / path


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest must be a JSON object: {path}")
    return manifest


def load_metadata_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata_path = manifest.get("metadata_path")
    if not metadata_path:
        return {}

    path = resolve_path(str(metadata_path))
    if not path.exists():
        return {}

    metadata = read_json(path)
    if not isinstance(metadata, dict):
        return {}
    return metadata


def infer_result_dir(
    manifest_path: Path | None,
    media_path: Path,
    output_dir: Path | None,
) -> Path:
    if output_dir:
        return output_dir

    if manifest_path:
        return manifest_path.parent.parent

    if media_path.parent.name == "resource":
        return media_path.parent.parent

    return media_path.parent.parent.parent


def resolve_manifest_path(path: Path) -> Path:
    return resolve_path(path_to_posix(path))
