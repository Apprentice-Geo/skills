from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping

if __package__:
    from ..process_logging import (
        ProcessLogger,
        SetupError,
        create_timestamped_log_path,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from process_logging import (
        ProcessLogger,
        SetupError,
        create_timestamped_log_path,
    )


DEFAULT_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
PYTHON_VERSION = (3, 12)


@dataclass(frozen=True)
class SetupPaths:
    root: Path
    cache_dir: Path
    logs_dir: Path
    uv_cache_dir: Path
    hf_home: Path
    hf_hub_cache: Path
    models_dir: Path
    results_dir: Path
    venv_dir: Path
    venv_python: Path
    requirements_path: Path
    qwen3_requirements_path: Path
    whisper_model_dir: Path
    qwen3_asr_model_dir: Path
    qwen3_aligner_model_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> "SetupPaths":
        root = root.resolve()
        cache_dir = root / ".cache"
        models_dir = root / "models"
        venv_dir = root / ".venv"
        return cls(
            root=root,
            cache_dir=cache_dir,
            logs_dir=cache_dir / "logs",
            uv_cache_dir=cache_dir / "uv",
            hf_home=cache_dir / "huggingface",
            hf_hub_cache=cache_dir / "huggingface" / "hub",
            models_dir=models_dir,
            results_dir=root / "results",
            venv_dir=venv_dir,
            venv_python=venv_dir / "Scripts" / "python.exe",
            requirements_path=root / "requirements.txt",
            qwen3_requirements_path=root / "requirements-qwen3.txt",
            whisper_model_dir=models_dir / "faster-whisper-small",
            qwen3_asr_model_dir=models_dir / "qwen3-asr-0.6b",
            qwen3_aligner_model_dir=models_dir / "qwen3-forcedaligner-0.6b",
        )


def configure_environment(
    paths: SetupPaths,
    environ: MutableMapping[str, str],
) -> None:
    for path in (
        paths.cache_dir,
        paths.logs_dir,
        paths.uv_cache_dir,
        paths.models_dir,
        paths.results_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    environ.setdefault("UV_CACHE_DIR", str(paths.uv_cache_dir))
    environ.setdefault("HF_HOME", str(paths.hf_home))
    environ.setdefault(
        "HUGGINGFACE_HUB_CACHE",
        str(Path(environ["HF_HOME"]) / "hub"),
    )
    environ.setdefault("PIP_INDEX_URL", DEFAULT_PIP_INDEX_URL)
    environ.setdefault("HF_ENDPOINT", DEFAULT_HF_ENDPOINT)

    Path(environ["HF_HOME"]).mkdir(parents=True, exist_ok=True)
    Path(environ["HUGGINGFACE_HUB_CACHE"]).mkdir(parents=True, exist_ok=True)


def create_log_path(paths: SetupPaths) -> Path:
    return create_timestamped_log_path(paths.logs_dir, "setup")


def assert_python_312(version: tuple[int, int, int], context: str) -> None:
    if version[:2] != PYTHON_VERSION:
        version_text = ".".join(str(part) for part in version)
        raise SetupError(
            f"{context} uses Python {version_text}; this setup requires Python 3.12."
        )


def read_python_version(python: Path, logger: ProcessLogger) -> tuple[int, int, int]:
    result = logger.run(
        [
            python,
            "-c",
            "import sys; print('.'.join(map(str, sys.version_info[:3])))",
        ],
        f"Check Python version for {python}",
    )
    try:
        major, minor, patch = result.output.strip().splitlines()[-1].split(".")
        return int(major), int(minor), int(patch)
    except (IndexError, ValueError) as exc:
        raise SetupError(f"Unable to read Python version from {python}.") from exc


def ensure_virtual_environment(
    paths: SetupPaths,
    bootstrap_python: Path,
    logger: ProcessLogger,
) -> None:
    if paths.venv_dir.exists():
        if not paths.venv_python.is_file():
            raise SetupError(
                f"Existing .venv is incomplete: {paths.venv_python} is missing. "
                "Remove it manually before rerunning setup."
            )
        assert_python_312(
            read_python_version(paths.venv_python, logger),
            "Existing .venv",
        )
        return

    assert_python_312(
        read_python_version(bootstrap_python, logger),
        "Setup launcher",
    )
    logger.run(
        [bootstrap_python, "-m", "venv", paths.venv_dir],
        "Create .venv",
        env=os.environ,
    )
    assert_python_312(
        read_python_version(paths.venv_python, logger),
        "Created .venv",
    )


def current_python() -> Path:
    return Path(sys.executable).resolve()
