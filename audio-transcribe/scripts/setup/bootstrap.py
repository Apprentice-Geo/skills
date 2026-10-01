from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from scripts.process_logging import ProcessLogger, SetupError, filesystem_cli
from scripts.runtime_paths import add_data_dir_argument


def run_setup(root: Path | None = None, *, data_dir: Path | None = None) -> Path:
    from scripts.setup.environment import (
        SetupPaths,
        assert_python_312,
        configure_environment,
        create_log_path,
        current_python,
        read_python_version,
    )
    from scripts.setup.install_core import (
        resolve_packaged_ffmpeg,
        verify_core_imports,
        verify_cpu_pytorch_build,
        verify_ffmpeg_executables,
    )

    root = root or Path(__file__).resolve().parents[2]
    paths = SetupPaths.from_root(root, data_dir)
    logger = ProcessLogger(create_log_path(paths))
    launcher_python = current_python()
    venv_python = paths.venv_python
    print(f"Full log: {logger.log_path}")
    try:
        configure_environment(paths, os.environ)
        logger.step(1, 3, "Verify setup Python 3.12")
        assert_python_312(
            read_python_version(launcher_python, logger),
            "Setup launcher",
        )

        logger.step(2, 3, "Sync default CPU dependencies")
        logger.run(
            ["uv", "sync", "--python", "3.12", "--no-dev", "--extra", "cpu"],
            "Sync default CPU dependencies",
            env=os.environ,
            cwd=root,
        )
        assert_python_312(read_python_version(venv_python, logger), "Existing .venv")

        logger.step(3, 3, "Verify core imports and packaged ffmpeg")
        verify_core_imports(venv_python, logger, os.environ)
        verify_cpu_pytorch_build(venv_python, logger, os.environ)
        ffmpeg, ffprobe = resolve_packaged_ffmpeg(
            venv_python,
            logger,
            os.environ,
        )
        verify_ffmpeg_executables(ffmpeg, ffprobe, logger)

        print("Setup completed.")
        return logger.log_path
    except Exception as exc:
        logger.logger.error("Setup failed: %s", exc, exc_info=exc)
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
    except SetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
