from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping

SKILL_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR_ENV = "BILI_AUDIOSUMMARY_DATA_DIR"


@dataclass(frozen=True)
class RuntimePaths:
    data_dir: Path

    @classmethod
    def resolve(
        cls, data_dir: Path | None = None, *, root: Path | None = None
    ) -> RuntimePaths:
        selected = data_dir or os.environ.get(DATA_DIR_ENV) or root or SKILL_ROOT
        return cls(Path(selected).expanduser().resolve())

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / ".cache" / "logs"

    @property
    def uv_cache_dir(self) -> Path:
        return self.data_dir / ".cache" / "uv"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    def configure_uv(self, environ: MutableMapping[str, str]) -> None:
        environ.setdefault("UV_CACHE_DIR", str(self.uv_cache_dir))


def add_data_dir_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir", type=Path, help=f"Runtime data root; overrides {DATA_DIR_ENV}."
    )
