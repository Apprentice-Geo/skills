from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from scripts.dependency_policy import (
    LANGUAGE_ID_IMPORTS,
    PYTORCH_PROBE,
    QWEN3_ASR_IMPORTS,
    parse_pytorch_probe,
)
from scripts.model_artifacts import (
    LANGUAGE_ID_REQUIRED_FILES,
    QWEN3_ASR_WEIGHT_PATTERNS,
    WHISPER_WEIGHT_PATTERNS,
    model_has_required_files,
)
from scripts.model_identity import MODEL_REVISIONS
from scripts.process_logging import ProcessLogger, SetupError
from scripts.setup.download_models import download_model
from scripts.setup.environment import (
    SetupPaths,
    assert_python_312,
    configure_environment,
    create_log_path,
    read_python_version,
)

WHISPER_MODEL_REPO = str(MODEL_REVISIONS["faster-whisper"]["repo"])
WHISPER_MODEL_REVISION = str(MODEL_REVISIONS["faster-whisper"]["revision"])
QWEN3_ASR_MODEL_REPO = str(MODEL_REVISIONS["qwen3-asr"]["repo"])
QWEN3_ASR_MODEL_REVISION = str(MODEL_REVISIONS["qwen3-asr"]["revision"])
QWEN3_ASR_ALIGNER_MODEL_REPO = str(MODEL_REVISIONS["qwen3-asr"]["aligner_repo"])
QWEN3_ASR_ALIGNER_MODEL_REVISION = str(MODEL_REVISIONS["qwen3-asr"]["aligner_revision"])
LANGUAGE_ID_MODEL_REPO = str(MODEL_REVISIONS["language-id"]["repo"])
LANGUAGE_ID_MODEL_REVISION = str(MODEL_REVISIONS["language-id"]["revision"])


def install_language_id_model(
    python: Path,
    paths: SetupPaths,
    logger: ProcessLogger,
) -> None:
    download_model(
        python,
        LANGUAGE_ID_MODEL_REPO,
        LANGUAGE_ID_MODEL_REVISION,
        paths.language_id_model_dir,
        LANGUAGE_ID_REQUIRED_FILES,
        logger,
        os.environ,
        require_all=True,
    )
    if not model_has_required_files(
        paths.language_id_model_dir, LANGUAGE_ID_REQUIRED_FILES
    ):
        raise SetupError(
            "Downloaded language identification model is incomplete: "
            f"{paths.language_id_model_dir}"
        )


def verify_qwen3_asr_environment(python: Path, logger: ProcessLogger) -> None:
    imports = LANGUAGE_ID_IMPORTS + QWEN3_ASR_IMPORTS
    try:
        logger.run(
            [python, "-c", "; ".join(f"import {module}" for module in imports)],
            "Verify Qwen3-ASR imports",
            env=os.environ,
        )
    except SetupError as exc:
        raise SetupError(
            "Qwen3-ASR dependencies are missing. Run "
            r"uv sync --python 3.12 --no-dev --extra qwen3-asr "
            "before installing Qwen3-ASR models."
        ) from exc
    result = logger.run(
        [python, "-c", PYTORCH_PROBE],
        "Verify Qwen3-ASR CUDA environment",
        env=os.environ,
    )
    try:
        probe = parse_pytorch_probe(result.output)
    except ValueError as exc:
        raise SetupError("Unable to inspect the installed PyTorch build.") from exc
    if probe["cuda_build"] is None:
        raise SetupError(
            "Qwen3-ASR requires a CUDA-enabled PyTorch build. Run "
            r"uv sync --python 3.12 --no-dev --extra qwen3-asr."
        )
    if not probe["cuda_available"]:
        raise SetupError(
            "Qwen3-ASR requires an available CUDA GPU and compatible driver; "
            f"the installed PyTorch build targets CUDA {probe['cuda_build']}."
        )


def install_faster_whisper_model(
    python: Path,
    paths: SetupPaths,
    logger: ProcessLogger,
) -> None:
    logger.step(3, 3, "Download and verify faster-whisper model")
    download_model(
        python,
        WHISPER_MODEL_REPO,
        WHISPER_MODEL_REVISION,
        paths.whisper_model_dir,
        WHISPER_WEIGHT_PATTERNS,
        logger,
        os.environ,
    )


def install_qwen_models(
    python: Path,
    paths: SetupPaths,
    logger: ProcessLogger,
) -> None:
    logger.step(4, 5, "Download and verify Qwen3-ASR model")
    download_model(
        python,
        QWEN3_ASR_MODEL_REPO,
        QWEN3_ASR_MODEL_REVISION,
        paths.qwen3_asr_model_dir,
        QWEN3_ASR_WEIGHT_PATTERNS,
        logger,
        os.environ,
    )

    logger.step(5, 5, "Download and verify Qwen3-ASR aligner model")
    download_model(
        python,
        QWEN3_ASR_ALIGNER_MODEL_REPO,
        QWEN3_ASR_ALIGNER_MODEL_REVISION,
        paths.qwen3_aligner_model_dir,
        QWEN3_ASR_WEIGHT_PATTERNS,
        logger,
        os.environ,
    )


def run_model_setup(model: str, root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[2]
    paths = SetupPaths.from_root(root)
    configure_environment(paths, os.environ)
    logger = ProcessLogger(create_log_path(paths))
    python = Path(sys.executable).resolve()
    print(f"Full log: {logger.log_path}")
    try:
        if python != paths.venv_python.resolve():
            raise SetupError(
                "Run model setup with "
                rf"uv run --no-sync python -m scripts.setup.install_model --model {model}"
            )

        total_steps = 3 if model == "faster-whisper" else 5
        logger.step(1, total_steps, "Verify uv-managed Python 3.12 environment")
        assert_python_312(read_python_version(python, logger), "Existing .venv")
        if model == "qwen3-asr":
            logger.step(2, total_steps, "Verify Qwen3-ASR CUDA environment")
            verify_qwen3_asr_environment(python, logger)
        language_step = 2 if model == "faster-whisper" else 3
        logger.step(language_step, total_steps, "Download and verify language ID model")
        install_language_id_model(python, paths, logger)

        if model == "faster-whisper":
            install_faster_whisper_model(python, paths, logger)
        elif model == "qwen3-asr":
            install_qwen_models(python, paths, logger)
        else:
            raise SetupError(f"Unsupported model: {model}")

        print(f"{model} model setup completed.")
        return logger.log_path
    except Exception:
        logger.logger.exception("Model setup failed")
        raise
    finally:
        logger.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download local ASR models.")
    parser.add_argument(
        "--model",
        required=True,
        choices=("faster-whisper", "qwen3-asr"),
        help="Model family to download.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_model_setup(args.model)
    except SetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
