from pathlib import Path

import scripts.validate_summary as validate_summary


def test_validate_summary_rejects_placeholders(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary\n\n{{title}}\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert "placeholder remains" in result.errors


def test_validate_summary_rejects_template_comments(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("<!-- Organize the summary by topic -->\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert "template comment remains" in result.errors


def test_validate_summary_accepts_clean_markdown(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("# 视频总结\n\n## 核心观点\n\n- 内容完整。\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert result.ok
    assert result.errors == []


def test_validate_summary_rejects_missing_file(tmp_path: Path) -> None:
    summary = tmp_path / "missing.md"

    result = validate_summary.validate_summary(summary)

    assert not result.ok
    assert result.errors == [f"summary file does not exist: {summary}"]


def test_validate_summary_rejects_non_utf8_file(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_bytes(b"\xff\xfe\x00")

    result = validate_summary.validate_summary(summary)

    assert not result.ok
    assert result.errors == ["summary file is not valid UTF-8"]


def test_validate_summary_reports_multiple_errors(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("{{title}}\n<!-- remove this -->\n", encoding="utf-8")

    result = validate_summary.validate_summary(summary)

    assert result.errors == [
        "placeholder remains",
        "template comment remains",
    ]


def test_main_prints_pass_message_for_valid_summary(tmp_path: Path, capsys) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("# Summary\n\n- Complete.\n", encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 0
    assert capsys.readouterr().out == "Summary validation passed.\n"


def test_main_prints_all_errors_for_invalid_summary(tmp_path: Path, capsys) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("{{title}}\n<!-- remove this -->\n", encoding="utf-8")

    exit_code = validate_summary.main([str(summary)])

    assert exit_code == 1
    assert capsys.readouterr().out == (
        "Summary validation failed:\n"
        "- placeholder remains\n"
        "- template comment remains\n"
    )
