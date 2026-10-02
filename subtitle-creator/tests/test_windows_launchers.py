import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.runtime_paths import DATA_DIR_ENV

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher argv parsing")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("launcher", ["check", "setup"])
@pytest.mark.parametrize("source", ["environment", "option", "equals"])
@pytest.mark.parametrize("forwarded_arg", ["value with spaces", r"D:\A&B", r"D:\A&B (copy)!"])
@pytest.mark.parametrize(
    "data_dir", ["D:\\", "D:\\\\", "D:\\data with spaces\\", "\\\\server\\share\\"]
)
def test_launcher_forwards_data_dir_to_python(
    tmp_path: Path, launcher: str, source: str, data_dir: str, forwarded_arg: str
):
    # 使用真实启动器与 Python 参数解析；隔离业务入口，不安装依赖或写入数据根目录。
    project = tmp_path / "skill"
    scripts = project / "scripts"
    (scripts / "setup").mkdir(parents=True)
    for relative in (
        "scripts/runtime_paths.bat",
        "scripts/check_dependencies.bat",
        "scripts/setup/setup_windows.bat",
    ):
        shutil.copy2(ROOT / relative, project / relative)
    (project / "pyproject.toml").touch()
    (project / "uv.lock").touch()
    python_dir = project / ".venv" / "Scripts"
    python_dir.mkdir(parents=True)
    shutil.copy2(sys.executable, python_dir / "python.exe")
    shutil.copy2(Path(sys.prefix) / "pyvenv.cfg", project / ".venv" / "pyvenv.cfg")
    (scripts / "__init__.py").touch()
    probe = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['ARGV_LOG']).write_text(json.dumps({"
        "'args': sys.argv[1:], 'cache': os.environ.get('UV_CACHE_DIR')}))\n"
        "sys.exit(7)\n"
    )
    (scripts / "check_dependencies.py").write_text(probe, encoding="utf-8")
    probe_path = tmp_path / "probe.py"
    probe_path.write_text(probe, encoding="utf-8")
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    (command_dir / "uv.cmd").write_text(
        '@echo off\nif "%1"=="python" exit /b 0\n'
        '"%TEST_PYTHON%" "%PROBE_SCRIPT%" %*\nexit /b %ERRORLEVEL%\n',
        encoding="ascii",
    )
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        (
            str(command_dir),
            str(
                Path(environment.get("SystemRoot", environment.get("SYSTEMROOT", r"C:\Windows")))
                / "System32"
            ),
        )
    )
    environment["TEST_PYTHON"] = sys.executable
    environment["PROBE_SCRIPT"] = str(probe_path)
    environment["ARGV_LOG"] = str(tmp_path / "argv.json")
    environment[DATA_DIR_ENV] = data_dir if source == "environment" else "D:\\ignored"
    environment.pop("UV_CACHE_DIR", None)
    # 手工构造 cmd 的带引号参数，覆盖用户输入 "D:\" 的形式。
    arguments = ""
    if source == "option":
        arguments = f'--data-dir "{data_dir}" '
    elif source == "equals":
        arguments = f'"--data-dir={data_dir}" '
    relative = (
        "scripts/check_dependencies.bat"
        if launcher == "check"
        else "scripts/setup/setup_windows.bat"
    )
    command = f'"{project / relative}" {arguments}--root "{forwarded_arg}" --sentinel "second&path"'
    result = subprocess.run(
        f'"{environment.get("COMSPEC", "cmd.exe")}" /d /s /c "{command}"',
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 7, result.stdout + result.stderr
    captured = json.loads(Path(environment["ARGV_LOG"]).read_text())
    args = captured["args"]
    assert args[-6:-1] == [
        "--root",
        forwarded_arg,
        "--sentinel",
        "second&path",
        "--data-dir",
    ]
    assert Path(args[-1]) == Path(data_dir)
    assert args.count("--data-dir") == 1
    assert Path(captured["cache"]) == Path(data_dir) / ".cache" / "uv"
