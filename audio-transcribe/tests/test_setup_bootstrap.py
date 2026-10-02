import logging
from pathlib import Path

import pytest

from scripts.process_logging import ProcessResult, SetupError
from scripts.setup import bootstrap, environment, install_core, install_model


@pytest.mark.parametrize("profile", [None, "cpu", "qwen3-asr"])
def test_setup_syncs_only_selected_environment_and_verifies_without_models(
    tmp_path, monkeypatch, profile
):
    commands = []
    verified = []
    loggers = []

    class Logger:
        def __init__(self, log_path):
            self.log_path = log_path
            self.logger = logging.getLogger(__name__)
            self.closed = False
            loggers.append(self)

        def step(self, *args):
            pass

        def run(self, command, *args, **kwargs):
            commands.append(command)
            return ProcessResult(0, "")

        def close(self):
            self.closed = True

    monkeypatch.setattr(bootstrap, "ProcessLogger", Logger)
    monkeypatch.setattr(environment, "read_python_version", lambda *args: (3, 12, 1))
    monkeypatch.setattr(
        install_core, "verify_core_imports", lambda *args: verified.append("core")
    )
    monkeypatch.setattr(
        install_core, "verify_cpu_pytorch_build", lambda *args: verified.append("cpu")
    )
    monkeypatch.setattr(
        install_model,
        "verify_qwen3_asr_environment",
        lambda *args: verified.append("qwen3-asr"),
    )
    monkeypatch.setattr(
        install_core,
        "resolve_packaged_ffmpeg",
        lambda *args: (Path("ffmpeg"), Path("ffprobe")),
    )
    monkeypatch.setattr(
        install_core,
        "verify_ffmpeg_executables",
        lambda *args: verified.append("ffmpeg"),
    )

    selected = profile or "cpu"
    kwargs = {} if profile is None else {"environment": profile}
    returned = bootstrap.run_setup(tmp_path, **kwargs)

    assert commands == [
        ["uv", "sync", "--python", "3.12", "--no-dev", "--extra", selected]
    ]
    assert verified == ["core", selected, "ffmpeg"]
    assert returned == loggers[0].log_path
    assert loggers[0].closed
    assert not (tmp_path / "models").exists()


def test_setup_cli_forwards_environment_and_data_dir(monkeypatch, tmp_path):
    received = []
    monkeypatch.setattr(
        bootstrap, "run_setup", lambda **kwargs: received.append(kwargs)
    )
    assert (
        bootstrap.main(["--environment", "qwen3-asr", "--data-dir", str(tmp_path)]) == 0
    )
    assert received == [{"environment": "qwen3-asr", "data_dir": tmp_path}]


def test_unsupported_environment_is_rejected_before_writes(tmp_path):
    with pytest.raises(SetupError, match="Unsupported setup environment"):
        bootstrap.run_setup(tmp_path, environment="unsupported")
    assert list(tmp_path.iterdir()) == []
