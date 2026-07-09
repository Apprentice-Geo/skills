import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.setup import install_model
from scripts.setup.environment import SetupPaths
from scripts.process_logging import SetupError


class RecordingLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.steps: list[str] = []
        self.runs: list[str] = []
        self.logger = self

    def step(self, _current, _total, message):
        self.steps.append(message)

    def run(self, _command, description, **_kwargs):
        self.runs.append(description)
        if description == "Verify Qwen3 imports":
            raise SetupError("missing qwen3")
        raise AssertionError(f"unexpected command: {description}")

    def exception(self, _message):
        pass

    def close(self):
        pass


def patch_model_setup_environment(monkeypatch, workspace_tmp_path: Path):
    paths = replace(
        SetupPaths.from_root(workspace_tmp_path),
        venv_python=Path(sys.executable).resolve(),
    )
    monkeypatch.setattr(
        install_model.SetupPaths,
        "from_root",
        classmethod(lambda _cls, _root: paths),
    )
    monkeypatch.setattr(
        install_model,
        "ProcessLogger",
        lambda log_path: RecordingLogger(log_path),
    )
    monkeypatch.setattr(
        install_model,
        "read_python_version",
        lambda *_args, **_kwargs: (3, 12, 0),
    )
    return paths


def test_faster_whisper_model_setup_downloads_default_model(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    paths = patch_model_setup_environment(monkeypatch, workspace_tmp_path)
    downloads: list[tuple[str, Path, tuple[str, ...]]] = []

    def fake_download_model(_python, repo_id, model_dir, patterns, *_args):
        downloads.append((repo_id, model_dir, tuple(patterns)))
        return True

    monkeypatch.setattr(install_model, "download_model", fake_download_model)

    install_model.run_model_setup("faster-whisper", workspace_tmp_path)

    assert downloads == [
        (
            "Systran/faster-whisper-small",
            paths.whisper_model_dir,
            ("model.bin",),
        )
    ]


def test_qwen3_model_setup_reports_missing_extra_dependencies(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    patch_model_setup_environment(monkeypatch, workspace_tmp_path)

    with pytest.raises(SetupError, match="uv sync --python 3.12 --no-dev --extra qwen3"):
        install_model.run_model_setup("qwen3", workspace_tmp_path)
