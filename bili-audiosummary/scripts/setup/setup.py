from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__:
    from .download_models import download_model
    from .environment import (
        SetupPaths,
        configure_environment,
        create_log_path,
        current_python,
        ensure_virtual_environment,
    )
    from .install_core import (
        install_requirements,
        resolve_packaged_ffmpeg,
        verify_core_imports,
        verify_ffmpeg_executables,
        verify_requirements,
    )
    from .process_logging import ProcessLogger, SetupError
else:
    from download_models import download_model
    from environment import (
        SetupPaths,
        configure_environment,
        create_log_path,
        current_python,
        ensure_virtual_environment,
    )
    from install_core import (
        install_requirements,
        resolve_packaged_ffmpeg,
        verify_core_imports,
        verify_ffmpeg_executables,
        verify_requirements,
    )
    from process_logging import ProcessLogger, SetupError


WHISPER_MODEL_REPO = "Systran/faster-whisper-small"


def run_setup(root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[2]
    paths = SetupPaths.from_root(root)
    configure_environment(paths, os.environ)
    logger = ProcessLogger(create_log_path(paths))
    print(f"Full log: {logger.log_path}")

    logger.step(1, 4, "Prepare Python 3.12 virtual environment")
    ensure_virtual_environment(paths, current_python(), logger)

    logger.step(2, 4, "Install and verify core requirements")
    install_requirements(
        paths.venv_python,
        paths.requirements_path,
        logger,
        os.environ,
    )
    verify_requirements(paths.venv_python, paths.requirements_path, logger)

    logger.step(3, 4, "Verify core imports and packaged ffmpeg")
    verify_core_imports(paths.venv_python, logger, os.environ)
    ffmpeg, ffprobe = resolve_packaged_ffmpeg(
        paths.venv_python,
        logger,
        os.environ,
    )
    verify_ffmpeg_executables(ffmpeg, ffprobe, logger)

    logger.step(4, 4, "Download and verify faster-whisper model")
    download_model(
        paths.venv_python,
        WHISPER_MODEL_REPO,
        paths.whisper_model_dir,
        ("model.bin",),
        logger,
        os.environ,
    )

    print("Setup completed.")
    return logger.log_path


def main() -> int:
    try:
        run_setup()
    except SetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
