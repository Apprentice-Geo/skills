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
