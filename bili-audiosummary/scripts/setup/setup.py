from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__:
    from ..process_logging import ProcessLogger, SetupError
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from process_logging import ProcessLogger, SetupError

if __package__:
    from .download_models import download_model
    from .environment import (
        SetupPaths,
        assert_python_312,
        configure_environment,
        create_log_path,
        current_python,
        read_python_version,
    )
    from .install_core import (
        resolve_packaged_ffmpeg,
        verify_core_imports,
        verify_ffmpeg_executables,
    )
else:
    from download_models import download_model
    from environment import (
        SetupPaths,
        assert_python_312,
        configure_environment,
        create_log_path,
        current_python,
        read_python_version,
    )
    from install_core import (
        resolve_packaged_ffmpeg,
        verify_core_imports,
        verify_ffmpeg_executables,
    )


WHISPER_MODEL_REPO = "Systran/faster-whisper-small"


def run_setup(root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[2]
    paths = SetupPaths.from_root(root)
    configure_environment(paths, os.environ)
    logger = ProcessLogger(create_log_path(paths))
    python = current_python()
    print(f"Full log: {logger.log_path}")
    try:
        logger.step(1, 3, "Verify uv-managed Python 3.12 environment")
        assert_python_312(read_python_version(python, logger), "Current environment")

        logger.step(2, 3, "Verify core imports and packaged ffmpeg")
        verify_core_imports(python, logger, os.environ)
        ffmpeg, ffprobe = resolve_packaged_ffmpeg(
            python,
            logger,
            os.environ,
        )
        verify_ffmpeg_executables(ffmpeg, ffprobe, logger)

        logger.step(3, 3, "Download and verify faster-whisper model")
        download_model(
            python,
            WHISPER_MODEL_REPO,
            paths.whisper_model_dir,
            ("model.bin",),
            logger,
            os.environ,
        )

        print("Setup completed.")
        return logger.log_path
    except Exception:
        logger.logger.exception("Setup failed")
        raise
    finally:
        logger.close()


def main() -> int:
    try:
        run_setup()
    except SetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
