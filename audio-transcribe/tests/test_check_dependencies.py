import json
from pathlib import Path

from scripts import check_dependencies
from scripts.setup.install_core import CORE_IMPORTS


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
    assert "audio_transcribe_contract" in CORE_IMPORTS
    assert any(
        item["id"] == "import:audio_transcribe_contract" for item in report["checks"]
    )
