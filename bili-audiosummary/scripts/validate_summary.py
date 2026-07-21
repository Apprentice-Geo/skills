import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

LANGUAGE_THRESHOLD = 0.8
SUMMARY_LANGUAGE_PATTERN = re.compile(r"_summary_(zh|en)\.md$", re.IGNORECASE)
CHINESE_CHARACTER_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ENGLISH_LETTER_PATTERN = re.compile(r"[A-Za-z]")


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str] = field(default_factory=list)


def expected_summary_language(summary_path: Path) -> str | None:
    match = SUMMARY_LANGUAGE_PATTERN.search(summary_path.name)
    return match.group(1).lower() if match else None


def prose_for_language_check(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"```.*?(?:```|\Z)", "", text, flags=re.DOTALL)
    text = re.sub(r"~~~.*?(?:~~~|\Z)", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "", text)
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"\]\([^\n)]*\)", "]", text)
    return re.sub(r"https?://[^\s)>]+", "", text)


def language_warning(text: str, expected_language: str) -> str | None:
    prose = prose_for_language_check(text)
    chinese_count = len(CHINESE_CHARACTER_PATTERN.findall(prose))
    english_count = len(ENGLISH_LETTER_PATTERN.findall(prose))
    language_character_count = chinese_count + english_count

    if expected_language == "zh":
        matching_count = chinese_count
        character_label = "Chinese characters"
        language_label = "Chinese"
    else:
        matching_count = english_count
        character_label = "English letters"
        language_label = "English"

    ratio = matching_count / language_character_count if language_character_count else 0.0
    if ratio >= LANGUAGE_THRESHOLD:
        return None

    return (
        f"summary language does not match the {expected_language} template: "
        f"{character_label} are {ratio:.1%} of the language characters; "
        f"rewrite the summary in {language_label}"
    )


def validate_summary(summary_path: Path) -> ValidationResult:
    if not summary_path.exists():
        return ValidationResult(False, [f"summary file does not exist: {summary_path}"])

    try:
        text = summary_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ValidationResult(False, ["summary file is not valid UTF-8"])

    errors: list[str] = []
    warnings: list[str] = []
    if re.search(r"\{\{[^}]+\}\}", text):
        errors.append("placeholder remains")
    if "<!--" in text or "-->" in text:
        errors.append("template comment remains")

    # 模板指定语言时检查语言
    expected_language = expected_summary_language(summary_path)
    if expected_language:
        warning = language_warning(text, expected_language)
        if warning:
            warnings.append(warning)

    return ValidationResult(not errors, errors, warnings)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a generated summary Markdown file.")
    parser.add_argument("summary_path", type=Path, help="Path to the final summary Markdown file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate_summary(args.summary_path)
    if result.ok:
        if result.warnings:
            print("Summary validation passed with warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
        else:
            print("Summary validation passed.")
        return 0

    print("Summary validation failed:")
    for error in result.errors:
        print(f"- {error}")
    if result.warnings:
        print("Summary validation warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
