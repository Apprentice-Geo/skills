import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_BAT = REPO_ROOT / "scripts" / "setup" / "setup_windows.bat"
DEFAULT_UV_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def write_fake_command(path: Path, name: str) -> None:
    path.write_text(
        "@echo off\n"
        f'>>"%SETUP_LAUNCH_LOG%" echo {name} %* [UV_CACHE_DIR=%UV_CACHE_DIR%] [UV_DEFAULT_INDEX=%UV_DEFAULT_INDEX%]\n'
        "echo Resolved 128 packages in 1ms\n"
        "echo Checked 107 packages in 12ms\n",
        encoding="ascii",
    )


def run_launcher(
    command_dir: Path,
    log_path: Path,
    *,
    uv_default_index: str | None = None,
) -> subprocess.CompletedProcess[str]:
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(command_dir), str(system32)))
    env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    env["SETUP_LAUNCH_LOG"] = str(log_path)
    env.pop("UV_CACHE_DIR", None)
    if uv_default_index is None:
        env.pop("UV_DEFAULT_INDEX", None)
    else:
        env["UV_DEFAULT_INDEX"] = uv_default_index
    return subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(SETUP_BAT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_setup_launcher_prepares_python_and_runs_setup(
    workspace_tmp_path: Path,
) -> None:
    command_dir = workspace_tmp_path / "bin"
    command_dir.mkdir()
    write_fake_command(command_dir / "uv.cmd", "uv")
    log_path = workspace_tmp_path / "launch.log"

    result = run_launcher(command_dir, log_path)

    assert result.returncode == 0
    invocation = log_path.read_text(encoding="utf-8")
    assert "uv python install 3.12" in invocation
    assert "uv run --python 3.12 --no-sync python" in invocation
    assert "-m scripts.setup.bootstrap" in invocation
    assert "uv sync --python 3.12 --no-dev" not in invocation
    assert str(REPO_ROOT / ".cache" / "uv") in invocation
    assert DEFAULT_UV_INDEX in invocation
    assert "uv python install 3.12" in result.stdout
    assert "Resolved 128 packages in 1ms" in result.stdout
    assert "Checked 107 packages in 12ms" in result.stdout


def test_setup_launcher_preserves_explicit_uv_default_index(
    workspace_tmp_path: Path,
) -> None:
    command_dir = workspace_tmp_path / "bin"
    command_dir.mkdir()
    write_fake_command(command_dir / "uv.cmd", "uv")
    log_path = workspace_tmp_path / "launch.log"
    custom_index = "https://example.invalid/simple"

    result = run_launcher(command_dir, log_path, uv_default_index=custom_index)

    assert result.returncode == 0
    invocation = log_path.read_text(encoding="utf-8")
    assert custom_index in invocation
    assert DEFAULT_UV_INDEX not in invocation


def test_setup_launcher_reports_missing_uv(workspace_tmp_path: Path) -> None:
    command_dir = workspace_tmp_path / "bin"
    command_dir.mkdir()
    log_path = workspace_tmp_path / "launch.log"

    result = run_launcher(command_dir, log_path)

    assert result.returncode != 0
    assert "setup requires uv" in result.stdout
