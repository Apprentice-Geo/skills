from __future__ import annotations

import argparse
import os
from pathlib import Path

from scripts.process_logging import (
    ProcessLogger,
    SetupError,
    create_timestamped_log_path,
    filesystem_cli,
)
from scripts.runtime_paths import RuntimePaths, add_data_dir_argument


def run_setup(root: Path | None = None, *, data_dir: Path | None = None) -> Path:
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    paths = RuntimePaths.resolve(data_dir, root=root)
    paths.configure_uv(os.environ)
    log_path = create_timestamped_log_path(paths.logs_dir, "setup")
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
        logger.step(2, 2, "Verify transcription contract version and API")
        logger.run(
            [
                "uv",
                "run",
                "--python",
                "3.12",
                "--no-sync",
                "python",
                "-m",
                "scripts.contract_check",
            ],
            "Verify transcription contract version and API",
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


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up the Skill environment.")
    add_data_dir_argument(parser)
    args = parser.parse_args(argv)
    try:
        run_setup(data_dir=args.data_dir)
    except SetupError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
