import os
import sys
from pathlib import Path

from scripts.setup.install_core import (
    resolve_packaged_ffmpeg,
    verify_core_imports,
    verify_ffmpeg_executables,
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
