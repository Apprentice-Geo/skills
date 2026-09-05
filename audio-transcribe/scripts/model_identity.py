from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.model_artifacts import (
    LANGUAGE_ID_REQUIRED_FILES,
    QWEN3_ASR_WEIGHT_PATTERNS,
    WHISPER_WEIGHT_PATTERNS,
    model_has_required_files,
    model_has_weights,
)

IDENTITY_MARKER = ".model_identity.json"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate model identity field.")
        result[key] = value
    return result


def installed_revision_matches(directory: Path, repo: str, revision: str) -> bool:
    try:
        marker = json.loads(
            (directory / IDENTITY_MARKER).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
        return marker == {"repo": repo, "revision": revision}
    except (OSError, UnicodeError, ValueError):
        return False


def validate_installation(
    directory: Path,
    repo: str,
    revision: str,
    patterns: tuple[str, ...] = (),
    required: tuple[str, ...] = (),
) -> dict[str, str]:
    expected = f"{repo}@{revision}"
    if not installed_revision_matches(directory, repo, revision):
        raise RuntimeError(
            f"Model identity marker missing, invalid or mismatched: {directory}; expected {expected}. Reinstall the model."
        )
    if not model_has_required_files(directory, required) or (
        patterns and not model_has_weights(directory, patterns)
    ):
        raise RuntimeError(
            f"Model files missing, empty or incomplete: {directory}; expected {expected}. Reinstall the model."
        )
    return {"repo": repo, "revision": revision}


def validate_model(
    provider: str, directory: Path, aligner_directory: Path | None = None
) -> dict[str, Any]:
    identity = provider_model_identity(provider)
    patterns = (
        WHISPER_WEIGHT_PATTERNS
        if provider == "faster-whisper"
        else QWEN3_ASR_WEIGHT_PATTERNS
    )
    required = LANGUAGE_ID_REQUIRED_FILES if provider == "language-id" else ()
    validate_installation(
        directory,
        identity["repo"],
        identity["revision"],
        () if required else patterns,
        required,
    )
    if provider == "qwen3-asr":
        if aligner_directory is None:
            raise ValueError("Qwen3-ASR requires an aligner directory.")
        validate_installation(
            aligner_directory,
            identity["aligner_repo"],
            identity["aligner_revision"],
            QWEN3_ASR_WEIGHT_PATTERNS,
        )
    return identity


# These revisions are shared by setup and result identity. Do not replace them
# with a floating branch: changing model bytes must produce a new config_digest.
MODEL_REVISIONS = {
    "faster-whisper": {
        "repo": "Systran/faster-whisper-small",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "logical_id": "faster-whisper-small",
    },
    "qwen3-asr": {
        "repo": "Qwen/Qwen3-ASR-0.6B",
        "revision": "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
        "logical_id": "qwen3-asr-0.6b",
        "aligner_repo": "Qwen/Qwen3-ForcedAligner-0.6B",
        "aligner_revision": "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
        "aligner_logical_id": "qwen3-forcedaligner-0.6b",
    },
    "language-id": {
        "repo": "speechbrain/lang-id-voxlingua107-ecapa",
        "revision": "0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9",
        "logical_id": "lang-id-voxlingua107-ecapa",
    },
}


def provider_model_identity(provider: str) -> dict[str, Any]:
    try:
        return dict(MODEL_REVISIONS[provider])
    except KeyError as exc:
        raise ValueError(f"Unsupported provider: {provider}") from exc
