import argparse
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_summary(summary_path: Path) -> ValidationResult:
    if not summary_path.exists():
        return ValidationResult(False, [f"summary file does not exist: {summary_path}"])

    try:
        text = summary_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ValidationResult(False, ["summary file is not valid UTF-8"])

    errors: list[str] = []
    if re.search(r"\{\{[^}]+\}\}", text):
        errors.append("placeholder remains")
    if "<!--" in text or "-->" in text:
        errors.append("template comment remains")

    return ValidationResult(not errors, errors)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a generated summary Markdown file.")
    parser.add_argument("summary_path", type=Path, help="Path to the final summary Markdown file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate_summary(args.summary_path)
    if result.ok:
        print("Summary validation passed.")
        return 0

    print("Summary validation failed:")
    for error in result.errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
