import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_BAT = REPO_ROOT / "scripts" / "setup" / "setup_windows.bat"


def write_fake_command(path: Path, name: str) -> None:
    path.write_text(
        "@echo off\n"
        f'>>"%SETUP_LAUNCH_LOG%" echo {name} %* [UV_CACHE_DIR=%UV_CACHE_DIR%]\n',
        encoding="ascii",
    )


def run_launcher(command_dir: Path, log_path: Path) -> subprocess.CompletedProcess[str]:
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(command_dir), str(system32)))
    env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    env["SETUP_LAUNCH_LOG"] = str(log_path)
    env.pop("UV_CACHE_DIR", None)
    return subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(SETUP_BAT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_setup_launcher_syncs_with_uv_and_runs_setup(workspace_tmp_path: Path) -> None:
    command_dir = workspace_tmp_path / "bin"
    command_dir.mkdir()
    write_fake_command(command_dir / "uv.cmd", "uv")
    log_path = workspace_tmp_path / "launch.log"

    result = run_launcher(command_dir, log_path)

    assert result.returncode == 0
    invocation = log_path.read_text(encoding="utf-8")
    assert "uv sync --python 3.12 --no-dev" in invocation
    assert "uv run --no-sync python" in invocation
    assert "scripts\\setup\\setup.py" in invocation
    assert str(REPO_ROOT / ".cache" / "uv") in invocation


def test_setup_launcher_reports_missing_uv(workspace_tmp_path: Path) -> None:
    command_dir = workspace_tmp_path / "bin"
    command_dir.mkdir()
    log_path = workspace_tmp_path / "launch.log"

    result = run_launcher(command_dir, log_path)

    assert result.returncode != 0
    assert "setup requires uv" in result.stdout
