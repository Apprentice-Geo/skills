import json
import subprocess
import sys
from pathlib import Path

from scripts import check_dependencies
from scripts.dependency_policy import CORE_IMPORTS


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
    assert report["skill"] == "bili-audiosummary"
    assert {"id", "status", "expected", "actual", "message", "fix"} <= set(
        report["checks"][0]
    )
    external = next(
        item for item in report["checks"] if item["id"] == "audio-transcribe"
    )
    assert external["status"] == "warn"
    assert "not checked" in external["actual"]
    contract = next(
        item
        for item in report["checks"]
        if item["id"] == "import:audio_transcribe_contract"
    )
    assert contract["status"] == "pass"
    checked_imports = {
        item["id"].removeprefix("import:")
        for item in report["checks"]
        if item["id"].startswith("import:")
    }
    assert set(CORE_IMPORTS) == checked_imports


def test_main_publishes_utf8_json_and_log_without_environment(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
        "skill": "bili-audiosummary",
        "overall_status": "ready",
        "checks": [],
        "providers": {},
        "logs": {},
    }
    monkeypatch.setattr(check_dependencies, "run_check", lambda _root: report)
    assert check_dependencies.main(["--root", str(tmp_path)]) == 0
    terminal = capsys.readouterr()
    output = terminal.out
    assert terminal.err == ""
    assert "JSON report:" in output
    paths = list((tmp_path / ".cache" / "logs").glob("dependency-check-*.json"))
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["skill"] == "bili-audiosummary"
    assert "environment" not in paths[0].read_text(encoding="utf-8").lower()


def test_main_compresses_pass_lines_in_terminal_but_keeps_them_in_log(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
        "skill": "bili-audiosummary",
        "overall_status": "not_ready",
        "checks": [
            {"id": "uv", "status": "pass", "message": "uv is ready"},
            {"id": "yt_dlp", "status": "fail", "message": "module failed"},
        ],
        "providers": {},
        "logs": {},
    }
    monkeypatch.setattr(check_dependencies, "run_check", lambda _root: report)

    assert check_dependencies.main(["--root", str(tmp_path)]) == 1
    terminal = capsys.readouterr()
    output = terminal.out
    log_path = next((tmp_path / ".cache" / "logs").glob("*.log"))
    log = log_path.read_text(encoding="utf-8")

    assert "[PASS] Dependencies OK (1 checks passed)" in output
    assert "[PASS] uv: uv is ready" not in output
    assert "[FAIL] yt_dlp: module failed" not in output
    assert terminal.err == "[FAIL] yt_dlp: module failed\n"
    assert "[PASS] uv: uv is ready" in log
