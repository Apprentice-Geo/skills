import importlib.util
import json
from pathlib import Path

import pytest

from scripts import check_dependencies
from scripts.dependency_policy import (
    BASE_IMPORTS,
    LANGUAGE_ID_IMPORTS,
    QWEN3_ASR_IMPORTS,
)


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
    if importlib.util.find_spec("qwen_asr") is None:
        pytest.skip("qwen3-asr optional dependencies are not installed")
    imported, actual, error = check_dependencies.check_module_import("qwen_asr")
    assert imported is True
    assert actual
    assert error == ""


def test_pytorch_build_check_runs_in_a_clean_process(monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(command):
        calls.append(command)
        return (
            0,
            json.dumps(
                {
                    "version": "2.7.1+cpu",
                    "cuda_build": None,
                    "cuda_available": False,
                }
            ),
            "",
        )

    monkeypatch.setattr(check_dependencies, "run_command", run)

    ok, probe, error = check_dependencies.check_pytorch_build()

    assert ok is True
    assert probe == {
        "version": "2.7.1+cpu",
        "cuda_build": None,
        "cuda_available": False,
    }
    assert error == ""
    assert calls[0][0] == check_dependencies.sys.executable
    assert calls[0][1] == "-c"


def test_report_has_provider_statuses_and_stable_shape() -> None:
    report = check_dependencies.run_check(Path(__file__).resolve().parents[1])
    assert set(report["providers"]) == {"faster-whisper", "qwen3-asr"}
    assert report["overall_status"] in {"ready", "degraded", "not_ready"}
    assert all(
        {"id", "status", "expected", "actual", "message", "fix"} <= set(item)
        for item in report["checks"]
    )
    assert "opencc" in BASE_IMPORTS
    assert set(LANGUAGE_ID_IMPORTS) == {"speechbrain", "torch", "torchaudio"}
    assert "qwen_asr" in QWEN3_ASR_IMPORTS
    checked_imports = {
        item["id"].removeprefix("import:")
        for item in report["checks"]
        if item["id"].startswith("import:")
    }
    assert set(BASE_IMPORTS + LANGUAGE_ID_IMPORTS) <= checked_imports


def test_missing_core_import_blocks_all_providers() -> None:
    import_status = {module: True for module in BASE_IMPORTS + LANGUAGE_ID_IMPORTS}
    import_status["opencc"] = False

    providers = check_dependencies.provider_readiness(
        import_status,
        ffmpeg_ok=True,
        language_ready=True,
        whisper_model_ready=True,
        qwen_imports=True,
        cuda_build=True,
        cuda_available=True,
        qwen_asr_model_ready=True,
        qwen_aligner_ready=True,
    )

    assert providers == {
        "faster-whisper": {"status": "not_ready"},
        "qwen3-asr": {"status": "not_ready"},
    }


@pytest.mark.parametrize(
    (
        "probe_ok",
        "probe",
        "build_status",
        "cuda_status",
        "cuda_build",
        "runtime",
        "expected_fix",
    ),
    [
        (
            True,
            {"version": "2.7.1+cpu", "cuda_build": None, "cuda_available": False},
            "pass",
            "warn",
            False,
            False,
            check_dependencies.CPU_SYNC_COMMAND,
        ),
        (
            True,
            {"version": "2.7.1+cu126", "cuda_build": "12.6", "cuda_available": False},
            "pass",
            "warn",
            True,
            False,
            check_dependencies.QWEN_SYNC_COMMAND,
        ),
        (
            True,
            {"version": "2.7.1+cu126", "cuda_build": "12.6", "cuda_available": True},
            "pass",
            "pass",
            True,
            True,
            check_dependencies.QWEN_SYNC_COMMAND,
        ),
        (
            False,
            {},
            "fail",
            "warn",
            False,
            False,
            None,
        ),
    ],
)
def test_pytorch_checker_maps_build_and_runtime(
    probe_ok,
    probe,
    build_status,
    cuda_status,
    cuda_build,
    runtime,
    expected_fix,
) -> None:
    checks, actual_build, actual_runtime = check_dependencies.pytorch_checks(
        probe_ok, probe, "torch import failed"
    )

    assert [check["id"] for check in checks] == [
        "pytorch:build",
        "provider:qwen3-asr:cuda",
    ]
    assert checks[0]["status"] == build_status
    assert checks[1]["status"] == cuda_status
    assert actual_build is cuda_build
    assert actual_runtime is runtime
    if expected_fix is None:
        assert "Choose one mutually exclusive profile" in checks[0]["fix"]
        assert check_dependencies.CPU_SYNC_COMMAND in checks[0]["fix"]
        assert check_dependencies.QWEN_SYNC_COMMAND in checks[0]["fix"]
    else:
        assert checks[0]["fix"] == expected_fix


@pytest.mark.parametrize(
    ("probe_ok", "probe", "expected_fix"),
    [
        (
            True,
            {"version": "2.7.1+cpu", "cuda_build": None, "cuda_available": False},
            check_dependencies.CPU_SYNC_COMMAND,
        ),
        (
            True,
            {
                "version": "2.7.1+cu126",
                "cuda_build": "12.6",
                "cuda_available": True,
            },
            check_dependencies.QWEN_SYNC_COMMAND,
        ),
    ],
)
def test_dependency_sync_fix_follows_detected_build(
    probe_ok, probe, expected_fix
) -> None:
    assert check_dependencies.dependency_sync_fix(probe_ok, probe) == expected_fix


@pytest.mark.parametrize(
    ("probe_result", "expected_fix"),
    [
        (
            (
                True,
                {
                    "version": "2.7.1+cpu",
                    "cuda_build": None,
                    "cuda_available": False,
                },
                "",
            ),
            check_dependencies.CPU_SYNC_COMMAND,
        ),
        (
            (
                True,
                {
                    "version": "2.7.1+cu126",
                    "cuda_build": "12.6",
                    "cuda_available": True,
                },
                "",
            ),
            check_dependencies.QWEN_SYNC_COMMAND,
        ),
        ((False, {}, "torch import failed"), None),
    ],
)
def test_shared_dependency_fixes_follow_pytorch_build(
    monkeypatch, tmp_path: Path, probe_result, expected_fix
) -> None:
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "uv.lock").touch()
    monkeypatch.setattr(check_dependencies.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        check_dependencies,
        "run_command",
        lambda command: (
            (0, "uv 0.8.17", "")
            if command == ["uv", "--version"]
            else (1, "", "dependency mismatch")
        ),
    )
    monkeypatch.setattr(
        check_dependencies,
        "check_module_import",
        lambda _module: (False, "import failed", "import failed"),
    )
    monkeypatch.setattr(
        check_dependencies,
        "check_pytorch_build",
        lambda: probe_result,
    )
    monkeypatch.setattr(
        check_dependencies,
        "ffmpeg_checks",
        lambda sync_fix: [
            check_dependencies.item(
                "ffmpeg-binaries",
                "fail",
                "packaged ffmpeg and ffprobe",
                "unavailable",
                "not installed",
                sync_fix,
            )
        ],
    )
    monkeypatch.setattr(
        check_dependencies,
        "model_check",
        lambda check_id, *_args, **_kwargs: check_dependencies.item(
            check_id, "fail", "model", "missing", "not installed"
        ),
    )

    report = check_dependencies.run_check(tmp_path)
    shared_fixes = {
        check["fix"]
        for check in report["checks"]
        if check["id"] == "uv-pip-check"
        or check["id"] == "ffmpeg-binaries"
        or check["id"].removeprefix("import:")
        in set(BASE_IMPORTS + LANGUAGE_ID_IMPORTS)
    }

    assert len(shared_fixes) == 1
    (actual_fix,) = shared_fixes
    if expected_fix is None:
        assert "Choose one mutually exclusive profile" in actual_fix
        assert check_dependencies.CPU_SYNC_COMMAND in actual_fix
        assert check_dependencies.QWEN_SYNC_COMMAND in actual_fix
    else:
        assert actual_fix == expected_fix


def test_provider_readiness_allows_whisper_in_cuda_environment() -> None:
    imports = {module: True for module in BASE_IMPORTS + LANGUAGE_ID_IMPORTS}

    providers = check_dependencies.provider_readiness(
        imports,
        ffmpeg_ok=True,
        language_ready=True,
        whisper_model_ready=True,
        qwen_imports=True,
        cuda_build=True,
        cuda_available=True,
        qwen_asr_model_ready=True,
        qwen_aligner_ready=True,
    )

    assert providers == {
        "faster-whisper": {"status": "ready"},
        "qwen3-asr": {"status": "ready"},
    }


def test_provider_readiness_requires_language_imports_for_whisper() -> None:
    imports = {module: True for module in BASE_IMPORTS + LANGUAGE_ID_IMPORTS}
    imports["torch"] = False

    providers = check_dependencies.provider_readiness(
        imports,
        ffmpeg_ok=True,
        language_ready=True,
        whisper_model_ready=True,
        qwen_imports=True,
        cuda_build=False,
        cuda_available=False,
        qwen_asr_model_ready=True,
        qwen_aligner_ready=True,
    )

    assert providers["faster-whisper"]["status"] == "not_ready"
    assert providers["qwen3-asr"]["status"] == "not_ready"


def test_main_compresses_pass_lines_in_terminal_but_keeps_them_in_log(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report = {
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
