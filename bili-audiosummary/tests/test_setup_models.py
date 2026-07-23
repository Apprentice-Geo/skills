from pathlib import Path

import pytest

from scripts.process_logging import ProcessResult, SetupError
from scripts.setup.download_models import download_model


class ModelDownloadLogger:
    def __init__(self, weight_path: Path | None = None) -> None:
        self.weight_path = weight_path
        self.calls: list[list[str]] = []

    def run(self, command, _description, **_kwargs):
        self.calls.append([str(part) for part in command])
        if self.weight_path is not None:
            self.weight_path.parent.mkdir(parents=True, exist_ok=True)
            self.weight_path.write_bytes(b"weights")
        return ProcessResult(0, "")


def test_download_model_uses_venv_python_and_validates_weights(
    workspace_tmp_path: Path,
) -> None:
    model_dir = workspace_tmp_path / "model"
    weight_path = model_dir / "model.bin"
    logger = ModelDownloadLogger(weight_path)
    python = workspace_tmp_path / "python.exe"

    downloaded = download_model(
        python,
        "example/model",
        model_dir,
        ("model.bin",),
        logger,
        {},
    )

    assert downloaded is True
    assert logger.calls[0][0] == str(python)
    assert "example/model" in logger.calls[0]
    assert str(model_dir) in logger.calls[0]


def test_download_model_stops_when_weights_are_missing(
    workspace_tmp_path: Path,
) -> None:
    with pytest.raises(SetupError, match="weights"):
        download_model(
            workspace_tmp_path / "python.exe",
            "example/model",
            workspace_tmp_path / "model",
            ("model.bin",),
            ModelDownloadLogger(),
            {},
        )
