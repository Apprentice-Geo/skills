import argparse
from dataclasses import dataclass
from pathlib import Path

from scripts.config import SKILL_ROOT
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    error,
    filesystem_cli,
    get_logger,
)
from scripts.process_logging import result as log_result
from scripts.runtime_paths import RuntimePaths, add_data_dir_argument

logger = get_logger(__name__)


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
    if not text.strip():
        errors.append("summary body is empty")
    return ValidationResult(not errors, errors)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a generated summary Markdown file."
    )
    parser.add_argument(
        "summary_path", type=Path, help="Path to the final summary Markdown file."
    )
    add_data_dir_argument(parser)
    return parser.parse_args(argv)


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = RuntimePaths.resolve(args.data_dir, root=SKILL_ROOT)
    log_path = create_timestamped_log_path(paths.logs_dir, "validate")
    with LoggingSession(log_path) as session:
        try:
            validation = validate_summary(args.summary_path)
        except (OSError, ValueError) as exc:
            session.report_failure(exc)
            return 1
        if validation.ok:
            log_result(logger, "Summary validation passed.")
            return 0

        message = "Summary validation failed:\n" + "\n".join(
            f"- {item}" for item in validation.errors
        )
        error(logger, "%s\nFull log: %s", message, log_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
