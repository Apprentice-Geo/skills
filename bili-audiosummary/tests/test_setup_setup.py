from pathlib import Path

from scripts.process_logging import ProcessResult
from scripts.setup import bootstrap, environment, install_core


class RecordingSetupLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.steps: list[str] = []
        self.calls: list[tuple[list[str], Path | None]] = []
        self.logger = self

    def step(self, _current, _total, message):
        self.steps.append(message)

    def run(self, command, _description, *, cwd=None, **_kwargs):
        self.calls.append(([str(part) for part in command], cwd))
        return ProcessResult(0, "")

    def exception(self, _message):
        pass

    def close(self):
        pass


def test_run_setup_syncs_dependencies_before_verifying_runtime(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    logger_holder: dict[str, RecordingSetupLogger] = {}

    def make_logger(log_path: Path) -> RecordingSetupLogger:
        logger = RecordingSetupLogger(log_path)
        logger_holder["logger"] = logger
        return logger

    checked_imports: list[Path] = []
    checked_ffmpeg: list[tuple[Path, Path]] = []

    monkeypatch.setattr(bootstrap, "ProcessLogger", make_logger)
    monkeypatch.setattr(
        environment,
        "read_python_version",
        lambda *_args, **_kwargs: (3, 12, 0),
    )
    monkeypatch.setattr(
        install_core,
        "verify_core_imports",
        lambda python, *_args, **_kwargs: checked_imports.append(python),
    )
    monkeypatch.setattr(
        install_core,
        "resolve_packaged_ffmpeg",
        lambda *_args, **_kwargs: (
            workspace_tmp_path / "ffmpeg.exe",
            workspace_tmp_path / "ffprobe.exe",
        ),
    )
    monkeypatch.setattr(
        install_core,
        "verify_ffmpeg_executables",
        lambda ffmpeg, ffprobe, *_args, **_kwargs: checked_ffmpeg.append(
            (ffmpeg, ffprobe)
        ),
    )

    bootstrap.run_setup(workspace_tmp_path)

    logger = logger_holder["logger"]
    assert (
        ["uv", "sync", "--python", "3.12", "--no-dev"],
        workspace_tmp_path,
    ) in logger.calls
    assert checked_imports == [
        workspace_tmp_path.resolve() / ".venv" / "Scripts" / "python.exe"
    ]
    assert checked_ffmpeg == [
        (workspace_tmp_path / "ffmpeg.exe", workspace_tmp_path / "ffprobe.exe")
    ]
