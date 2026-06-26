import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.setup import install_qwen3
from scripts.setup.environment import SetupPaths
from process_logging import SetupError


class RecordingLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.steps: list[str] = []
        self.logger = self

    def step(self, _current, _total, message):
        self.steps.append(message)

    def run(self, _command, description, **_kwargs):
        if description == "Verify Qwen3 imports":
            raise SetupError("missing qwen3")
        raise AssertionError(f"unexpected command: {description}")

    def exception(self, _message):
        pass

    def close(self):
        pass


def test_qwen3_setup_reports_missing_extra_dependencies(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    paths = replace(
        SetupPaths.from_root(workspace_tmp_path),
        venv_python=Path(sys.executable).resolve(),
    )
    monkeypatch.setattr(
        install_qwen3.SetupPaths,
        "from_root",
        classmethod(lambda _cls, _root: paths),
    )
    monkeypatch.setattr(
        install_qwen3,
        "ProcessLogger",
        lambda log_path: RecordingLogger(log_path),
    )
    monkeypatch.setattr(
        install_qwen3,
        "read_python_version",
        lambda *_args, **_kwargs: (3, 12, 0),
    )

    with pytest.raises(SetupError, match="uv sync --python 3.12 --no-dev --extra qwen3"):
        install_qwen3.run_qwen3_setup(workspace_tmp_path)
