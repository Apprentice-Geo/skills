import json
from pathlib import Path
from typing import Sequence

WHISPER_WEIGHT_PATTERNS = ("model.bin",)
QWEN3_ASR_WEIGHT_PATTERNS = ("model*.safetensors",)
LANGUAGE_ID_REQUIRED_FILES = (
    "embedding_model.ckpt",
    "classifier.ckpt",
    "hyperparams.yaml",
    "label_encoder.txt",
)


def model_has_weights(model_dir: Path, patterns: Sequence[str]) -> bool:
    if not model_dir.is_dir():
        return False
    matched = [
        path
        for pattern in patterns
        for path in model_dir.glob(pattern)
        if path.is_file()
    ]
    if not matched:
        return False
    if not any(path.suffix == ".safetensors" for path in matched):
        return True
    single_file = model_dir / "model.safetensors"
    index_path = model_dir / "model.safetensors.index.json"
    if single_file.is_file() and not index_path.exists():
        return True
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        weight_map = index["weight_map"]
        if not isinstance(weight_map, dict) or not weight_map:
            return False
        shard_names = set(weight_map.values())
        if not all(isinstance(name, str) and name for name in shard_names):
            return False
        model_root = model_dir.resolve()
        shard_paths = []
        for name in shard_names:
            shard_path = (model_dir / name).resolve()
            shard_path.relative_to(model_root)
            shard_paths.append(shard_path)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, RuntimeError):
        return False
    return all(path.is_file() for path in shard_paths)


def model_has_required_files(model_dir: Path, filenames: Sequence[str]) -> bool:
    return model_dir.is_dir() and all(
        (model_dir / filename).is_file() for filename in filenames
    )
