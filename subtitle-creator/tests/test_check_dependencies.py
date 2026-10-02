import json
import subprocess
import sys
from pathlib import Path

from scripts import check_dependencies


def test_missing_packaging_does_not_prevent_cli_report(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    code = """
import builtins
import sys

original_import = builtins.__import__

def without_packaging(name, *args, **kwargs):
    if name == "packaging" or name.startswith("packaging."):
        raise ModuleNotFoundError("No module named 'packaging'", name="packaging")
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_packaging
from scripts import check_dependencies

check_dependencies._run = lambda command: (0, "available", "")
if hasattr(check_dependencies, "_ffmpeg_checks"):
    check_dependencies._ffmpeg_checks = lambda: []
raise SystemExit(check_dependencies.main(["--data-dir", sys.argv[1]]))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    json_path = next((tmp_path / ".cache" / "logs").glob("dependency-check-*.json"))
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "not_ready"
    checks = {item["id"]: item for item in report["checks"]}
    version = checks["contract:version"]
    assert version["status"] == "fail"
    assert "packaging" in version["message"]
    assert "uv sync" in version["fix"]
    assert checks["contract:api"]["status"] == "pass"
    assert "[FAIL] contract:version:" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert json_path.with_suffix(".log").is_file()


def test_report_has_stable_shape_and_external_skill_is_not_checked() -> None:
    report = check_dependencies.run_check(Path(__file__).resolve().parents[1])

    assert report["skill"] == "subtitle-creator"
    assert all(
        {"id", "status", "expected", "actual", "message", "fix"} <= set(item)
        for item in report["checks"]
    )
    external = next(item for item in report["checks"] if item["id"] == "audio-transcribe")
    assert external["status"] == "warn"


def test_main_publishes_json_and_log(monkeypatch, tmp_path: Path, capsys) -> None:
    report = {
        "skill": "subtitle-creator",
        "overall_status": "ready",
        "checks": [],
        "logs": {},
    }
    monkeypatch.setattr(check_dependencies, "run_check", lambda _root: report)

    assert check_dependencies.main(["--root", str(tmp_path)]) == 0
    assert "JSON report:" in capsys.readouterr().out
    json_path = next((tmp_path / ".cache" / "logs").glob("dependency-check-*.json"))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["skill"] == "subtitle-creator"
    assert len(list((tmp_path / ".cache" / "logs").glob("dependency-check-*.log"))) == 1


def test_main_keeps_pass_details_in_log_and_routes_problems_to_stderr(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
        "skill": "subtitle-creator",
        "overall_status": "not_ready",
        "checks": [
            {"id": "uv", "status": "pass", "message": "uv is ready"},
            {"id": "external", "status": "warn", "message": "check separately"},
            {"id": "contract", "status": "fail", "message": "import failed"},
        ],
        "logs": {},
    }
    monkeypatch.setattr(check_dependencies, "run_check", lambda _root: report)

    assert check_dependencies.main(["--root", str(tmp_path)]) == 1

    terminal = capsys.readouterr()
    assert "[PASS] Dependencies OK (1 checks passed)" in terminal.out
    assert "[PASS] uv: uv is ready" not in terminal.out
    assert terminal.err == ("[WARN] external: check separately\n[FAIL] contract: import failed\n")
    log_path = next((tmp_path / ".cache" / "logs").glob("dependency-check-*.log"))
    log_text = log_path.read_text(encoding="utf-8")
    assert "[PASS] uv: uv is ready" in log_text
    assert "[WARN] external: check separately" in log_text
    assert "[FAIL] contract: import failed" in log_text
