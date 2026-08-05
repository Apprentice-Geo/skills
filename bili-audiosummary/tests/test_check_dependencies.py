import json
from pathlib import Path

from scripts import check_dependencies


def test_report_has_stable_shape_and_external_skill_is_not_checked() -> None:
    report = check_dependencies.run_check(Path(__file__).resolve().parents[1])
    assert report["schema_version"] == 1
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


def test_main_publishes_utf8_json_and_log_without_environment(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
        "schema_version": 1,
        "skill": "bili-audiosummary",
        "overall_status": "ready",
        "checks": [],
        "providers": {},
        "logs": {},
    }
    monkeypatch.setattr(check_dependencies, "run_check", lambda _root: report)
    assert check_dependencies.main(["--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "JSON report:" in output
    paths = list((tmp_path / ".cache" / "logs").glob("dependency-check-*.json"))
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "environment" not in paths[0].read_text(encoding="utf-8").lower()


def test_main_compresses_pass_lines_in_terminal_but_keeps_them_in_log(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
        "schema_version": 1,
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
    output = capsys.readouterr().out
    log_path = next((tmp_path / ".cache" / "logs").glob("*.log"))
    log = log_path.read_text(encoding="utf-8")

    assert "[PASS] Dependencies OK (1 checks passed)" in output
    assert "[PASS] uv: uv is ready" not in output
    assert "[FAIL] yt_dlp: module failed" in output
    assert "[PASS] uv: uv is ready" in log
