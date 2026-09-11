import json
from pathlib import Path

from scripts import check_dependencies


def test_report_has_stable_shape_and_external_skill_is_not_checked() -> None:
    report = check_dependencies.run_check(Path(__file__).resolve().parents[1])

    assert report["schema_version"] == 1
    assert report["skill"] == "subtitle-creator"
    assert all(
        {"id", "status", "expected", "actual", "message", "fix"} <= set(item)
        for item in report["checks"]
    )
    external = next(item for item in report["checks"] if item["id"] == "audio-transcribe")
    assert external["status"] == "warn"


def test_main_publishes_json_and_log(monkeypatch, tmp_path: Path, capsys) -> None:
    report = {
        "schema_version": 1,
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
    assert payload["schema_version"] == 1
    assert len(list((tmp_path / ".cache" / "logs").glob("dependency-check-*.log"))) == 1


def test_main_keeps_pass_details_in_log_and_routes_problems_to_stderr(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
        "schema_version": 1,
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
