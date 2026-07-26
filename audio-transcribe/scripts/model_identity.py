from __future__ import annotations

from typing import Any

# These revisions are shared by setup and result identity. Do not replace them
# with a floating branch: changing model bytes must produce a new variant_id.
MODEL_REVISIONS = {
    "faster-whisper": {
        "repo": "Systran/faster-whisper-small",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "logical_id": "faster-whisper-small",
    },
    "qwen3": {
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
