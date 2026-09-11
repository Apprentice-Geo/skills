from __future__ import annotations

import os
from pathlib import Path

from scripts.process_logging import (
    ProcessLogger,
    SetupError,
    create_timestamped_log_path,
)


def run_setup(root: Path | None = None) -> Path:
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    log_path = create_timestamped_log_path(root / ".cache" / "logs", "setup")
    logger = ProcessLogger(log_path)
    try:
        logger.result("Full log: %s", logger.log_path)
        logger.step(1, 2, "Sync runtime dependencies")
        logger.run(
            ["uv", "sync", "--python", "3.12", "--no-dev"],
            "Sync runtime dependencies",
            env=os.environ,
            cwd=root,
        )
        logger.step(2, 2, "Verify transcription contract import")
        logger.run(
            [
                "uv",
                "run",
                "--python",
                "3.12",
                "--no-sync",
                "python",
                "-c",
                "import audio_transcribe_contract",
            ],
            "Verify transcription contract import",
            env=os.environ,
            cwd=root,
        )
        logger.result("Setup completed.")
        return logger.log_path
    except Exception as exc:
        logger.report_failure(exc)
        if not isinstance(exc, SetupError):
            raise SetupError(f"Setup failed. See {logger.log_path}") from exc
        raise
    finally:
        logger.close()


def main() -> int:
    try:
        run_setup()
    except SetupError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
