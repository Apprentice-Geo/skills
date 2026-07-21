from pathlib import Path

import scripts.validate_summary as validate_summary


def test_validate_summary_rejects_placeholders(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("# Summary\n\n{{title}}\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert "placeholder remains" in result.errors


def test_validate_summary_rejects_template_comments(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("<!-- Organize the summary by topic -->\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert "template comment remains" in result.errors


def test_validate_summary_accepts_clean_markdown(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("# 视频总结\n\n## 核心观点\n\n- 内容完整。\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert result.ok
    assert result.errors == []


def test_validate_summary_accepts_matching_template_languages(
    workspace_tmp_path: Path,
) -> None:
    chinese_summary = workspace_tmp_path / "BVTEST_summary_zh.md"
    english_summary = workspace_tmp_path / "BVTEST_summary_en.md"
    chinese_summary.write_text("# 视频总结\n\n这是完整内容。\n", encoding="utf-8")
    english_summary.write_text(
        "# Video Summary\n\nThis is complete.\n", encoding="utf-8"
    )

    assert validate_summary.validate_summary(chinese_summary).ok
    assert validate_summary.validate_summary(english_summary).ok


def test_validate_summary_accepts_language_ratio_at_eighty_percent(
    workspace_tmp_path: Path,
) -> None:
    chinese_summary = workspace_tmp_path / "BVTEST_summary_zh.md"
    english_summary = workspace_tmp_path / "BVTEST_summary_en.md"
    chinese_summary.write_text("中文内容完全正确ab", encoding="utf-8")
    english_summary.write_text("abcdefgh中文", encoding="utf-8")

    assert validate_summary.validate_summary(chinese_summary).ok
    assert validate_summary.validate_summary(english_summary).ok


def test_validate_summary_warns_language_ratio_below_eighty_percent(
    workspace_tmp_path: Path,
) -> None:
    summary = workspace_tmp_path / "BVTEST_summary_zh.md"
    summary.write_text("中文内容完全正确abc", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert result.ok
    assert result.errors == []
    assert result.warnings == [
        "summary language does not match the zh template: Chinese characters are "
        "72.7% of the language characters; rewrite the summary in Chinese"
    ]


def test_validate_summary_ignores_non_prose_content_for_language_ratio(
    workspace_tmp_path: Path,
) -> None:
    summary = workspace_tmp_path / "BVTEST_summary_zh.md"
    summary.write_text(
        "# 中文总结\n\n"
        "这是完整内容。\n\n"
        "[来源](https://example.com/english/path)\n\n"
        "`english_code`\n\n"
        "```python\nprint('english code')\n```\n",
        encoding="utf-8",
    )

    assert validate_summary.validate_summary(summary).ok


def test_validate_summary_warns_known_template_without_language_characters(
    workspace_tmp_path: Path,
) -> None:
    summary = workspace_tmp_path / "BVTEST_summary_en.md"
    summary.write_text("# 123\n\n- 456\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert result.ok
    assert result.errors == []
    assert result.warnings == [
        "summary language does not match the en template: English letters are "
        "0.0% of the language characters; rewrite the summary in English"
    ]


def test_validate_summary_keeps_language_check_optional_for_other_filenames(
    workspace_tmp_path: Path,
) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("# 123\n", encoding="utf-8")

    assert validate_summary.validate_summary(summary).ok


def test_validate_summary_rejects_missing_file(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "missing.md"

    result = validate_summary.validate_summary(summary)

    assert not result.ok
    assert result.errors == [f"summary file does not exist: {summary}"]


def test_validate_summary_rejects_non_utf8_file(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_bytes(b"\xff\xfe\x00")

    result = validate_summary.validate_summary(summary)

    assert not result.ok
    assert result.errors == ["summary file is not valid UTF-8"]


def test_validate_summary_reports_multiple_errors(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("{{title}}\n<!-- remove this -->\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert result.errors == [
        "placeholder remains",
        "template comment remains",
    ]


def test_validate_summary_reports_language_warning_separately_from_errors(
    workspace_tmp_path: Path,
) -> None:
    summary = workspace_tmp_path / "BVTEST_summary_zh.md"
    summary.write_text(
        "{{title}}\n<!-- remove this -->\nEnglish text\n", encoding="utf-8"
    )

    result = validate_summary.validate_summary(summary)

    assert result.errors == [
        "placeholder remains",
        "template comment remains",
    ]
    assert result.warnings == [
        "summary language does not match the zh template: Chinese characters are "
        "0.0% of the language characters; rewrite the summary in Chinese",
    ]


def test_main_prints_pass_message_for_valid_summary(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("# Summary\n\n- Complete.\n", encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 0
    assert capsys.readouterr().out == "Summary validation passed.\n"


def test_main_prints_all_errors_for_invalid_summary(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("{{title}}\n<!-- remove this -->\n", encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 1
    assert capsys.readouterr().out == (
        "Summary validation failed:\n"
        "- placeholder remains\n"
        "- template comment remains\n"
    )


def test_main_warns_about_template_language_without_failing(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    summary = workspace_tmp_path / "BVTEST_summary_en.md"
    summary.write_text("# 视频总结\n\n这是完整内容。\n", encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "Summary validation passed with warnings:\n"
        "- summary language does not match the en template: English letters are "
        "0.0% of the language characters; rewrite the summary in English\n"
    )
