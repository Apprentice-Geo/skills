from pathlib import Path
from typing import Sequence

WHISPER_WEIGHT_PATTERNS = ("model.bin",)
QWEN3_WEIGHT_PATTERNS = ("model*.safetensors",)
LANGUAGE_ID_REQUIRED_FILES = (
    "embedding_model.ckpt",
    "classifier.ckpt",
    "hyperparams.yaml",
    "label_encoder.txt",
)


def model_has_weights(model_dir: Path, patterns: Sequence[str]) -> bool:
    return model_dir.is_dir() and any(
        path.is_file() for pattern in patterns for path in model_dir.glob(pattern)
    )


def model_has_required_files(model_dir: Path, filenames: Sequence[str]) -> bool:
    return model_dir.is_dir() and all(
        (model_dir / filename).is_file() for filename in filenames
    )
