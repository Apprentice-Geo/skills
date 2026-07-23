from pathlib import Path

import pytest

from scripts.model_artifacts import (
    QWEN3_WEIGHT_PATTERNS,
    WHISPER_WEIGHT_PATTERNS,
    model_has_weights,
)


@pytest.mark.parametrize(
    ("artifact_name", "whisper_ready", "qwen3_ready"),
    [
        ("model.bin", True, False),
        ("model.safetensors", False, True),
        ("model-00001-of-00002.safetensors", False, True),
    ],
)
def test_model_has_weights_uses_provider_specific_patterns(
    workspace_tmp_path: Path,
    artifact_name: str,
    whisper_ready: bool,
    qwen3_ready: bool,
) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / artifact_name).write_bytes(b"weights")

    assert model_has_weights(model_dir, WHISPER_WEIGHT_PATTERNS) is whisper_ready
    assert model_has_weights(model_dir, QWEN3_WEIGHT_PATTERNS) is qwen3_ready


@pytest.mark.parametrize(
    "patterns",
    [WHISPER_WEIGHT_PATTERNS, QWEN3_WEIGHT_PATTERNS],
)
def test_model_has_weights_rejects_empty_or_missing_directories(
    workspace_tmp_path: Path,
    patterns: tuple[str, ...],
) -> None:
    empty_dir = workspace_tmp_path / "empty"
    empty_dir.mkdir()

    assert not model_has_weights(empty_dir, patterns)
    assert not model_has_weights(workspace_tmp_path / "missing", patterns)


@pytest.mark.parametrize(
    ("artifact_name", "patterns"),
    [
        ("model.bin", WHISPER_WEIGHT_PATTERNS),
        ("model.safetensors", QWEN3_WEIGHT_PATTERNS),
    ],
)
def test_model_has_weights_rejects_matching_directories(
    workspace_tmp_path: Path,
    artifact_name: str,
    patterns: tuple[str, ...],
) -> None:
    model_dir = workspace_tmp_path / "model"
    (model_dir / artifact_name).mkdir(parents=True)

    assert not model_has_weights(model_dir, patterns)
