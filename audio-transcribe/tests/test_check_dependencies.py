import json
from pathlib import Path

from scripts import check_dependencies
from scripts.dependency_policy import CORE_IMPORTS


def test_model_check_requires_revision_marker_and_required_files(
    tmp_path: Path,
) -> None:
    model = tmp_path / "language-id"
    model.mkdir()
    required = ("embedding_model.ckpt", "classifier.ckpt")
    for name in required:
        (model / name).write_bytes(b"model")
    identity = {"repo": "owner/model", "revision": "a" * 40}
    result = check_dependencies.model_check("model:test", model, (), identity, required)
    assert result["status"] == "fail"
    (model / ".model_identity.json").write_text(json.dumps(identity), encoding="utf-8")
    result = check_dependencies.model_check("model:test", model, (), identity, required)
    assert result["status"] == "pass"


def test_qwen_import_check_runs_in_a_clean_process() -> None:
    imported, actual, error = check_dependencies.check_module_import("qwen_asr")
    assert imported is True
    assert actual
    assert error == ""


def test_report_has_provider_statuses_and_stable_shape() -> None:
    report = check_dependencies.run_check(Path(__file__).resolve().parents[1])
    assert report["schema_version"] == 1
    assert set(report["providers"]) == {"faster-whisper", "qwen3-asr"}
    assert report["overall_status"] in {"ready", "degraded", "not_ready"}
    assert all(
        {"id", "status", "expected", "actual", "message", "fix"} <= set(item)
        for item in report["checks"]
    )
    assert "opencc" in CORE_IMPORTS
    checked_imports = {
        item["id"].removeprefix("import:")
        for item in report["checks"]
        if item["id"].startswith("import:")
    }
    assert set(CORE_IMPORTS) <= checked_imports


def test_missing_core_import_blocks_all_providers() -> None:
    import_status = {module: True for module in CORE_IMPORTS}
    import_status["opencc"] = False

    providers = check_dependencies.provider_readiness(
        import_status,
        ffmpeg_ok=True,
        language_ready=True,
        whisper_model_ready=True,
        qwen_imports=True,
        cuda=True,
        qwen_asr_model_ready=True,
        qwen_aligner_ready=True,
    )

    assert providers == {
        "faster-whisper": {"status": "not_ready"},
        "qwen3-asr": {"status": "not_ready"},
    }


def test_main_compresses_pass_lines_in_terminal_but_keeps_them_in_log(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
        "schema_version": 1,
        "skill": "audio-transcribe",
        "overall_status": "not_ready",
        "checks": [
            {"id": "uv", "status": "pass", "message": "uv is ready"},
            {"id": "torch", "status": "fail", "message": "module failed"},
        ],
        "providers": {"faster-whisper": {"status": "not_ready"}},
        "logs": {},
    }
    monkeypatch.setattr(check_dependencies, "run_check", lambda _root: report)

    assert check_dependencies.main(["--root", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    log_path = next((tmp_path / ".cache" / "logs").glob("*.log"))
    log = log_path.read_text(encoding="utf-8")

    assert "[PASS] Dependencies OK (1 checks passed)" in output
    assert "[PASS] uv: uv is ready" not in output
    assert "[FAIL] torch: module failed" in output
    assert "Provider faster-whisper: not_ready" in output
    assert "[PASS] uv: uv is ready" in log
