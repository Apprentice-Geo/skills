import scripts.config as config


def test_default_subtitle_language_is_chinese() -> None:
    assert config.DEFAULT_SUBTITLE_LANGUAGE == "zh"
