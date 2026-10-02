import sys
from types import ModuleType

import pytest

from scripts import check_dependencies, import_compat
from scripts.dependency_policy import LANGUAGE_ID_IMPORTS, QWEN3_ASR_IMPORTS


def test_only_known_lazy_aliases_are_removed(monkeypatch):
    lazy_type = type(
        "LazyModule", (ModuleType,), {"__module__": "speechbrain.utils.importutils"}
    )
    lazy = lazy_type("speechbrain.k2_integration")
    real = ModuleType("speechbrain.wordemb")
    unrelated = lazy_type("unrelated")
    monkeypatch.setitem(sys.modules, "speechbrain.k2_integration", lazy)
    monkeypatch.setitem(sys.modules, "speechbrain.wordemb", real)
    monkeypatch.setitem(sys.modules, "unrelated", unrelated)
    import_compat.clear_optional_aliases()
    assert "speechbrain.k2_integration" not in sys.modules
    assert sys.modules["speechbrain.wordemb"] is real
    assert sys.modules["unrelated"] is unrelated


def test_speechbrain_aliases_are_cleared_before_next_import(monkeypatch):
    lazy_type = type(
        "LazyModule", (ModuleType,), {"__module__": "speechbrain.utils.importutils"}
    )

    def importing(name):
        if name == "speechbrain":
            monkeypatch.setitem(
                sys.modules, "speechbrain.k2_integration", lazy_type("alias")
            )
        else:
            assert "speechbrain.k2_integration" not in sys.modules
        return ModuleType(name)

    monkeypatch.setattr(import_compat.importlib, "import_module", importing)
    import_compat.import_dependency("speechbrain")
    import_compat.import_dependency("qwen_asr")


def test_import_exception_is_preserved(monkeypatch):
    failure = RuntimeError("binary compatibility failure")

    def importing(name):
        raise failure

    monkeypatch.setattr(import_compat.importlib, "import_module", importing)
    with pytest.raises(RuntimeError) as captured:
        import_compat.import_dependency("qwen_asr")
    assert captured.value is failure


@pytest.mark.parametrize(
    "name,category",
    [
        ("qwen_asr", "required-dependency-missing"),
        ("k2", "optional-module/compatibility"),
    ],
)
def test_missing_dependency_diagnostics(name, category):
    missing = ModuleNotFoundError("missing dependency", name=name)
    failure = ImportError("lazy import failed")
    failure.__cause__ = missing
    diagnostic = import_compat.describe_import_error("qwen_asr", failure)
    assert f"missing_module={name}" in diagnostic
    assert category in diagnostic
    assert "ImportError" in diagnostic and "ModuleNotFoundError" in diagnostic


def test_process_launch_failure_is_not_reported_as_missing_package(monkeypatch):
    monkeypatch.setattr(
        check_dependencies,
        "run_command",
        lambda command: (1, "", "PermissionError: denied"),
    )
    ok, actual, error = check_dependencies.check_import_sequence(("qwen_asr",))
    assert not ok
    assert "Probe process failed" in actual
    assert "PermissionError" in error
    assert "required-dependency-missing" not in error


def test_combined_failure_blocks_qwen_but_preserves_whisper(monkeypatch, tmp_path):
    monkeypatch.setattr(
        check_dependencies, "check_module_import", lambda name: (True, "importable", "")
    )
    monkeypatch.setattr(
        check_dependencies,
        "check_import_sequence",
        lambda names: (False, "import conflict", "import conflict"),
    )
    monkeypatch.setattr(
        check_dependencies,
        "check_pytorch_build",
        lambda: (
            True,
            {"version": "2", "cuda_build": "12.6", "cuda_available": True},
            "",
        ),
    )
    monkeypatch.setattr(
        check_dependencies,
        "model_check",
        lambda check_id, *args, **kwargs: check_dependencies.item(
            check_id, "pass", "model", "ready", "ready"
        ),
    )
    monkeypatch.setattr(
        check_dependencies,
        "ffmpeg_checks",
        lambda fix: [
            check_dependencies.item("ffmpeg", "pass", "binary", "ready", "ready")
        ],
    )
    report = check_dependencies.run_check(tmp_path)
    assert report["providers"]["qwen3-asr"]["status"] == "not_ready"
    assert report["providers"]["faster-whisper"]["status"] == "ready"


def test_real_continuous_import_in_existing_qwen_environment():
    import importlib.util

    if importlib.util.find_spec("qwen_asr") is None:
        pytest.skip("Qwen optional dependencies are not installed")
    ok, actual, error = check_dependencies.check_import_sequence(
        LANGUAGE_ID_IMPORTS + QWEN3_ASR_IMPORTS
    )
    assert ok, error
    assert actual == "importable"


def test_probe_logs_complete_traceback_and_retains_original_diagnostic(
    monkeypatch, caplog
):
    import json

    diagnostic = "importing=qwen_asr; ImportError: conflict; missing_module=k2"
    traceback = "Traceback (most recent call last):\n" + "context\n" * 100 + diagnostic
    monkeypatch.setattr(
        check_dependencies,
        "run_command",
        lambda command: (1, json.dumps({"error": diagnostic}), traceback),
    )
    with caplog.at_level("DEBUG", logger="audio_transcribe.dependency_probe"):
        ok, actual, error = check_dependencies.check_import_sequence(("qwen_asr",))
    assert not ok and actual == error == diagnostic
    assert traceback in caplog.text


def test_import_error_with_module_name_is_not_a_missing_dependency():
    failure = ImportError("cannot import name changed_api", name="qwen_asr")
    diagnostic = import_compat.describe_import_error("qwen_asr", failure)
    assert "category=import-compatibility" in diagnostic
    assert "required-dependency-missing" not in diagnostic
    assert "changed_api" in diagnostic


@pytest.mark.parametrize("payload", ["[]", "{}", '{"result": false}', "not JSON"])
def test_invalid_probe_output_is_not_ready(monkeypatch, payload):
    monkeypatch.setattr(
        check_dependencies, "run_command", lambda command: (0, payload, "")
    )
    ok, _, error = check_dependencies.check_import_sequence(("qwen_asr",))
    assert not ok
    assert "invalid output" in error
