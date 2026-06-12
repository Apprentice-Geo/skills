from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

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
        read_python_version,
    )
    from .install_core import install_requirements, verify_requirements
else:
    from download_models import download_model
    from environment import (
        SetupPaths,
        assert_python_312,
        configure_environment,
        create_log_path,
        read_python_version,
    )
    from install_core import install_requirements, verify_requirements


QWEN3_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu126"
QWEN3_ASR_MODEL_REPO = "Qwen/Qwen3-ASR-0.6B"
QWEN3_ALIGNER_MODEL_REPO = "Qwen/Qwen3-ForcedAligner-0.6B"


def install_cuda_torch(
    python: Path,
    logger: ProcessLogger,
    env: Mapping[str, str],
) -> None:
    logger.run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--index-url",
            QWEN3_TORCH_INDEX_URL,
            "torch",
            "torchaudio",
            "--disable-pip-version-check",
            "--progress-bar",
            "off",
        ],
        "Install CUDA torch and torchaudio",
        env=env,
    )


def run_qwen3_setup(root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[2]
    paths = SetupPaths.from_root(root)
    configure_environment(paths, os.environ)
    logger = ProcessLogger(create_log_path(paths))
    python = Path(sys.executable).resolve()
    print(f"Full log: {logger.log_path}")
    try:
        if python != paths.venv_python.resolve():
            raise SetupError(
                "Run Qwen3 setup with "
                r".\.venv\Scripts\python.exe scripts\setup\install_qwen3.py"
            )
        assert_python_312(read_python_version(python, logger), "Existing .venv")

        logger.step(1, 4, "Install Qwen3 CUDA dependencies")
        install_cuda_torch(python, logger, os.environ)
        install_requirements(
            python,
            paths.qwen3_requirements_path,
            logger,
            os.environ,
        )
        verify_requirements(python, paths.qwen3_requirements_path, logger)

        logger.step(2, 4, "Verify Qwen3 imports")
        logger.run(
            [python, "-c", "import qwen_asr; import torch; import torchaudio"],
            "Verify Qwen3 imports",
            env=os.environ,
        )

        logger.step(3, 4, "Download and verify Qwen3 ASR model")
        download_model(
            python,
            QWEN3_ASR_MODEL_REPO,
            paths.qwen3_asr_model_dir,
            ("model*.safetensors",),
            logger,
            os.environ,
        )

        logger.step(4, 4, "Download and verify Qwen3 aligner model")
        download_model(
            python,
            QWEN3_ALIGNER_MODEL_REPO,
            paths.qwen3_aligner_model_dir,
            ("model*.safetensors",),
            logger,
            os.environ,
        )

        print("Qwen3 setup completed.")
        return logger.log_path
    except Exception:
        logger.logger.exception("Qwen3 setup failed")
        raise
    finally:
        logger.close()


def main() -> int:
    try:
        run_qwen3_setup()
    except SetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
