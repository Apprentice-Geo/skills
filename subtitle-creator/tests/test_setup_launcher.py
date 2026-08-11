import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only setup launcher")

ROOT = Path(__file__).resolve().parents[1]
SETUP_BAT = ROOT / "scripts" / "setup" / "setup_windows.bat"


def _run_launcher(
    tmp_path: Path, *, provide_uv: bool
) -> tuple[subprocess.CompletedProcess[str], Path]:
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    log_path = tmp_path / "launch.log"
    if provide_uv:
        (command_dir / "uv.cmd").write_text(
            '@echo off\n>>"%SETUP_LAUNCH_LOG%" echo uv %*\n', encoding="ascii"
        )
    env = os.environ.copy()
    windows_dir = Path(env.get("SystemRoot", env.get("WINDIR", r"C:\Windows")))
    env["PATH"] = os.pathsep.join((str(command_dir), str(windows_dir / "System32")))
    env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    env["SETUP_LAUNCH_LOG"] = str(log_path)
    env.pop("UV_CACHE_DIR", None)
    result = subprocess.run(
        [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(SETUP_BAT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log_path


def test_setup_launcher_syncs_runtime_environment(tmp_path: Path) -> None:
    result, log_path = _run_launcher(tmp_path, provide_uv=True)

    assert result.returncode == 0
    invocations = log_path.read_text(encoding="utf-8")
    assert "uv python install 3.12" in invocations
    assert "uv sync --python 3.12 --no-dev" in invocations
    assert "uv run --python 3.12 --no-sync python -c" in invocations


def test_setup_launcher_reports_missing_uv(tmp_path: Path) -> None:
    result, _ = _run_launcher(tmp_path, provide_uv=False)

    assert result.returncode != 0
    assert "setup requires uv" in result.stdout
