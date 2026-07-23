from pathlib import Path
from typing import Sequence

WHISPER_WEIGHT_PATTERNS = ("model.bin",)
QWEN3_WEIGHT_PATTERNS = ("model*.safetensors",)


def model_has_weights(model_dir: Path, patterns: Sequence[str]) -> bool:
    return model_dir.is_dir() and any(
        path.is_file() for pattern in patterns for path in model_dir.glob(pattern)
    )
