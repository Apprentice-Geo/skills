from __future__ import annotations

import json
from typing import Any

BASE_IMPORTS = (
    "audio_transcribe_contract",
    "faster_whisper",
    "ffmpeg_binaries",
    "numpy",
    "opencc",
)

LANGUAGE_ID_IMPORTS = (
    "speechbrain",
    "torch",
    "torchaudio",
)

QWEN3_ASR_IMPORTS = (
    "accelerate",
    "huggingface_hub",
    "librosa",
    "numba",
    "qwen_asr",
    "soundfile",
    "transformers",
)

PYTORCH_PROBE = """
import json
import torch

print(json.dumps({
    "version": str(torch.__version__),
    "cuda_build": torch.version.cuda,
    "cuda_available": bool(torch.cuda.is_available()),
}))
"""


def parse_pytorch_probe(output: str) -> dict[str, str | bool | None]:
    try:
        payload: Any = json.loads(output.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ValueError("PyTorch probe did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("PyTorch probe must return an object")
    version = payload.get("version")
    cuda_build = payload.get("cuda_build")
    cuda_available = payload.get("cuda_available")
    if (
        not isinstance(version, str)
        or (cuda_build is not None and not isinstance(cuda_build, str))
        or not isinstance(cuda_available, bool)
    ):
        raise ValueError("PyTorch probe returned invalid fields")
    return {
        "version": version,
        "cuda_build": cuda_build,
        "cuda_available": cuda_available,
    }
