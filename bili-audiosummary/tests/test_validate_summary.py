from pathlib import Path

import scripts.validate_summary as validate_summary


def valid_summary(language: str, body: str) -> str:
    return "# Summary\n\n" + "\n\n".join(
        f"## {heading}\n\n{body}"
        for heading in validate_summary.required_sections(language)
    )


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
    chinese_summary.write_text(valid_summary("zh", "这是完整内容。"), encoding="utf-8")
    english_summary.write_text(
        valid_summary("en", "This is complete."), encoding="utf-8"
    )

    assert validate_summary.validate_summary(chinese_summary).ok
    assert validate_summary.validate_summary(english_summary).ok


def test_language_warning_at_threshold() -> None:
    assert validate_summary.language_warning("中文内容完全正确ab", "zh") is None
    assert validate_summary.language_warning("abcdefgh中文", "en") is None


def test_language_warning_below_threshold() -> None:
    assert validate_summary.language_warning("中文内容完全正确abc", "zh") == (
        "summary language does not match the zh template: Chinese characters are "
        "72.7% of the language characters; rewrite the summary in Chinese"
    )


def test_language_warning_ignores_code_and_links() -> None:
    assert (
        validate_summary.language_warning(
            "这是完整内容。 [来源](https://example.com/english/path) "
            "`english_code`\n```python\nprint('english code')\n```",
            "zh",
        )
        is None
    )


def test_language_warning_without_characters() -> None:
    assert "0.0%" in (validate_summary.language_warning("123", "en") or "")


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

    assert result.errors[:2] == [
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(validate_summary, "SKILL_ROOT", workspace_tmp_path)
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("# Summary\n\n- Complete.\n", encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 0
    assert capsys.readouterr().out == "Summary validation passed.\n"


def test_main_prints_all_errors_for_invalid_summary(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr(validate_summary, "SKILL_ROOT", workspace_tmp_path)
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("{{title}}\n<!-- remove this -->\n", encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 1
    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err.startswith(
        "Summary validation failed:\n"
        "- placeholder remains\n"
        "- template comment remains\n"
    )
    assert "Full log:" in terminal.err


def test_main_warns_about_template_language_without_failing(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr(validate_summary, "SKILL_ROOT", workspace_tmp_path)
    summary = workspace_tmp_path / "BVTEST_summary_en.md"
    summary.write_text(valid_summary("en", "这是完整内容。" * 100), encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 0
    terminal = capsys.readouterr()
    assert terminal.out == "Summary validation passed with warnings:\n"
    assert terminal.err.startswith("- summary language does not match the en template:")


def test_main_logs_runtime_read_error_without_terminal_traceback(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr(validate_summary, "SKILL_ROOT", workspace_tmp_path)
    monkeypatch.setattr(
        validate_summary,
        "validate_summary",
        lambda _path: (_ for _ in ()).throw(PermissionError("read denied")),
    )

    assert validate_summary.main([str(workspace_tmp_path / "summary.md")]) == 1

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err.startswith("Error: read denied\nFull log: ")
    assert "Traceback" not in terminal.err
    logs = list((workspace_tmp_path / ".cache" / "logs").glob("validate-*.log"))
    assert len(logs) == 1
    log_text = logs[0].read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in log_text
    assert "PermissionError: read denied" in log_text


def test_validation_rejects_empty_body(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "summary.md"
    summary.write_text("  \n", encoding="utf-8")
    assert "summary body is empty" in validate_summary.validate_summary(summary).errors


def test_validation_requires_filled_template_sections(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "BVTEST_summary_zh.md"
    summary.write_text("# 标题\n\n## 一句话总结\n\n", encoding="utf-8")
    result = validate_summary.validate_summary(summary)
    assert "required section is empty: 一句话总结" in result.errors
    assert "required section is missing: 核心要点" in result.errors


def test_optional_sections_are_optional(workspace_tmp_path: Path) -> None:
    summary = workspace_tmp_path / "BVTEST_summary_zh.md"
    summary.write_text(valid_summary("zh", "有效总结内容。"), encoding="utf-8")
    assert validate_summary.validate_summary(summary).ok
    assert "广告内容" not in validate_summary.required_sections("zh")
