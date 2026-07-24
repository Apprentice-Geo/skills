from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping

from scripts.process_logging import (
    ProcessLogger,
    SetupError,
    create_timestamped_log_path,
)

PYTHON_VERSION = (3, 12)


@dataclass(frozen=True)
class SetupPaths:
    root: Path
    cache_dir: Path
    logs_dir: Path
    uv_cache_dir: Path
    results_dir: Path
    venv_dir: Path
    venv_python: Path

    @classmethod
    def from_root(cls, root: Path) -> "SetupPaths":
        root = root.resolve()
        cache_dir = root / ".cache"
        venv_dir = root / ".venv"
        return cls(
            root=root,
            cache_dir=cache_dir,
            logs_dir=cache_dir / "logs",
            uv_cache_dir=cache_dir / "uv",
            results_dir=root / "results",
            venv_dir=venv_dir,
            venv_python=venv_dir / "Scripts" / "python.exe",
        )


def configure_environment(
    paths: SetupPaths,
    environ: MutableMapping[str, str],
) -> None:
    for path in (
        paths.cache_dir,
        paths.logs_dir,
        paths.uv_cache_dir,
        paths.results_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    environ.setdefault("UV_CACHE_DIR", str(paths.uv_cache_dir))


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


def current_python() -> Path:
    return Path(sys.executable).resolve()
