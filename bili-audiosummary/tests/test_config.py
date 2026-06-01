import config


def test_default_model_directories_live_under_root_models_dir() -> None:
    models_dir = config.SKILL_ROOT / "models"

    assert config.DEFAULT_WHISPER_MODEL_DIR == models_dir / "faster-whisper-small"
    assert config.QWEN3_ASR_MODEL_DIR == models_dir / "qwen3-asr-0.6b"
    assert config.QWEN3_ALIGNER_MODEL_DIR == models_dir / "qwen3-forcedaligner-0.6b"
    assert "tools" not in config.DEFAULT_WHISPER_MODEL_DIR.relative_to(config.SKILL_ROOT).parts
