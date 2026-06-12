import importlib.metadata
import os
import sys
from pathlib import Path

import pytest

from scripts.setup.install_core import (
    OFFICIAL_PYPI_URL,
    install_requirements,
    resolve_packaged_ffmpeg,
    verify_core_imports,
    verify_ffmpeg_executables,
    verify_requirements,
)
from process_logging import ProcessLogger, ProcessResult, SetupError


class RecordingLogger:
    def __init__(self, results: list[ProcessResult] | None = None) -> None:
        self.results = list(results or [ProcessResult(0, "")])
        self.calls: list[tuple[list[str], dict[str, str], bool]] = []

    def run(self, command, _description, *, env=None, check=True, **_kwargs):
        self.calls.append(
            ([str(part) for part in command], dict(env or {}), check)
        )
        result = self.results.pop(0)
        if check and result.returncode:
            raise SetupError("command failed")
        return result


def write_requirement(workspace_tmp_path: Path, requirement: str) -> Path:
    path = workspace_tmp_path / "requirements.txt"
    path.write_text(f"{requirement}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("requirement", "valid"),
    [
        (f"pytest=={importlib.metadata.version('pytest')}", True),
        ("pytest==0", False),
        (f"pytest>={importlib.metadata.version('pytest')},<9999", True),
        ("pytest<0", False),
        ("pytest", True),
        ("pytest-mock", True),
    ],
)
def test_verify_requirements_follows_declared_constraints(
    workspace_tmp_path: Path,
    requirement: str,
    valid: bool,
) -> None:
    path = write_requirement(workspace_tmp_path, requirement)
    logger = ProcessLogger(workspace_tmp_path / "setup.log")

    if valid:
        verify_requirements(Path(sys.executable), path, logger)
    else:
        with pytest.raises(SetupError, match=requirement):
            verify_requirements(Path(sys.executable), path, logger)


def test_install_requirements_uses_one_pip_requirements_command(
    workspace_tmp_path: Path,
) -> None:
    requirements_path = write_requirement(workspace_tmp_path, "pytest")
    python = workspace_tmp_path / "python.exe"
    logger = RecordingLogger()
    env = {"PIP_INDEX_URL": "https://mirror.invalid/simple"}

    install_requirements(python, requirements_path, logger, env)

    assert len(logger.calls) == 1
    command, command_env, check = logger.calls[0]
    assert command[:4] == [str(python), "-m", "pip", "install"]
    assert command[command.index("-r") + 1] == str(requirements_path)
    assert command_env == env
    assert check is False


def test_install_requirements_retries_official_pypi(
    workspace_tmp_path: Path,
) -> None:
    requirements_path = write_requirement(workspace_tmp_path, "pytest")
    logger = RecordingLogger(
        [ProcessResult(1, "mirror failed"), ProcessResult(0, "installed")]
    )
    env = {"PIP_INDEX_URL": "https://mirror.invalid/simple"}

    install_requirements(
        workspace_tmp_path / "python.exe",
        requirements_path,
        logger,
        env,
    )

    assert len(logger.calls) == 2
    first_command, first_env, first_check = logger.calls[0]
    retry_command, retry_env, retry_check = logger.calls[1]
    assert first_command == retry_command
    assert first_env["PIP_INDEX_URL"] == "https://mirror.invalid/simple"
    assert first_check is False
    assert retry_env["PIP_INDEX_URL"] == OFFICIAL_PYPI_URL
    assert retry_check is True


def test_verify_core_imports_checks_installed_runtime_modules(
    workspace_tmp_path: Path,
) -> None:
    modules_dir = workspace_tmp_path / "modules"
    modules_dir.mkdir()
    for name in ("yt_dlp", "faster_whisper", "ffmpeg_binaries"):
        (modules_dir / f"{name}.py").write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(modules_dir)
    logger = ProcessLogger(workspace_tmp_path / "setup.log")

    verify_core_imports(Path(sys.executable), logger, env)


def test_resolve_packaged_ffmpeg_returns_package_binaries(
    workspace_tmp_path: Path,
) -> None:
    modules_dir = workspace_tmp_path / "modules"
    modules_dir.mkdir()
    bin_dir = workspace_tmp_path / "ffmpeg"
    bin_dir.mkdir()
    ffmpeg = bin_dir / "ffmpeg.exe"
    ffprobe = bin_dir / "ffprobe.exe"
    ffmpeg.write_bytes(b"")
    ffprobe.write_bytes(b"")
    (modules_dir / "ffmpeg_binaries.py").write_text(
        f"FFMPEG_PATH = r'{ffmpeg}'\n"
        "def init():\n"
        "    pass\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(modules_dir)
    logger = ProcessLogger(workspace_tmp_path / "setup.log")

    resolved = resolve_packaged_ffmpeg(Path(sys.executable), logger, env)

    assert resolved == (ffmpeg, ffprobe)


def test_verify_ffmpeg_executables_checks_both_tools(
    workspace_tmp_path: Path,
) -> None:
    ffmpeg = workspace_tmp_path / "ffmpeg.exe"
    ffprobe = workspace_tmp_path / "ffprobe.exe"
    logger = RecordingLogger([ProcessResult(0, ""), ProcessResult(0, "")])

    verify_ffmpeg_executables(ffmpeg, ffprobe, logger)

    assert [call[0] for call in logger.calls] == [
        [str(ffmpeg), "-version"],
        [str(ffprobe), "-version"],
    ]
