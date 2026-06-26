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
        read_python_version,
    )
else:
    from download_models import download_model
    from environment import (
        SetupPaths,
        assert_python_312,
        configure_environment,
        create_log_path,
        read_python_version,
    )


QWEN3_ASR_MODEL_REPO = "Qwen/Qwen3-ASR-0.6B"
QWEN3_ALIGNER_MODEL_REPO = "Qwen/Qwen3-ForcedAligner-0.6B"


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
                r"uv run --no-sync python scripts\setup\install_qwen3.py"
            )
        assert_python_312(read_python_version(python, logger), "Existing .venv")

        logger.step(1, 3, "Verify Qwen3 extra imports")
        try:
            logger.run(
                [python, "-c", "import qwen_asr; import torch; import torchaudio"],
                "Verify Qwen3 imports",
                env=os.environ,
            )
        except SetupError as exc:
            raise SetupError(
                "Qwen3 dependencies are missing. Run "
                r"uv sync --python 3.12 --no-dev --extra qwen3 "
                "before Qwen3 setup."
            ) from exc

        logger.step(2, 3, "Download and verify Qwen3 ASR model")
        download_model(
            python,
            QWEN3_ASR_MODEL_REPO,
            paths.qwen3_asr_model_dir,
            ("model*.safetensors",),
            logger,
            os.environ,
        )

        logger.step(3, 3, "Download and verify Qwen3 aligner model")
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
