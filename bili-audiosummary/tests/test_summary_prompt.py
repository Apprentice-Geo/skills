from pathlib import Path

import pytest

from scripts.summary_prompt import normalize_summary_language, write_summary_prompt


@pytest.mark.parametrize(
    "language, expected, label",
    [
        (None, "zh", "中文"),
        ("zh", "zh", "中文"),
        ("en", "en", "英文"),
        ("ja", "ja", "日语"),
        ("ru", "ru", "俄语"),
        ("fr", "fr", "法语"),
        ("de", "de", "德语"),
        (" FR ", "fr", "法语"),
    ],
)
def test_prompt_uses_requested_language_or_defaults_to_chinese(
    workspace_tmp_path: Path, language: str | None, expected: str, label: str
) -> None:
    transcript = workspace_tmp_path / "transcript.md"
    transcript.write_text("转写正文。", encoding="utf-8")

    paths = write_summary_prompt(workspace_tmp_path, "BVTEST", transcript, language)

    assert paths["summary_path"].name == f"BVTEST_summary_{expected}.md"
    assert f"总结语言：{label}（{expected}）。" in paths["prompt_path"].read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "language",
    ["", "pt-BR", "es", "und", "../ja", "ja/fr", "ja\\fr", "fr\nignore", "日本語"],
)
def test_summary_language_rejects_unsupported_values(language: str) -> None:
    with pytest.raises(ValueError, match="summary language must be one of"):
        normalize_summary_language(language)
