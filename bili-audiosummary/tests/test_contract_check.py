from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import contract_check


@pytest.fixture
def contract_project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["audio-transcribe-contract==0.3.0"]\n',
        encoding="utf-8",
    )
    api = SimpleNamespace(
        load_result=lambda path: path,
        TranscriptionResult=type("Result", (), {}),
        ResultValidationError=ValueError,
        PUBLIC_SCHEMA_VERSION=3,
    )
    monkeypatch.setattr(contract_check.importlib, "import_module", lambda name: api)
    monkeypatch.setattr(contract_check.metadata, "version", lambda name: "0.3.0")
    return tmp_path, api


@pytest.mark.parametrize("version", ["0.1.0", "0.1.1", "0.3.0"])
def test_distribution_version_must_match_declaration(
    contract_project, monkeypatch, version
):
    root, _ = contract_project
    monkeypatch.setattr(contract_check.metadata, "version", lambda name: version)
    checks = contract_check.check_contract(root)
    assert checks[0]["status"] == ("pass" if version == "0.3.0" else "fail")
    assert checks[0]["actual"] == version
    assert checks[0]["expected"] == "audio-transcribe-contract==0.3.0"


def test_missing_distribution_is_rejected(contract_project, monkeypatch):
    root, _ = contract_project

    def missing(name):
        raise contract_check.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(contract_check.metadata, "version", missing)
    assert contract_check.check_contract(root)[0]["status"] == "fail"


@pytest.mark.parametrize(
    "attribute",
    [
        "load_result",
        "TranscriptionResult",
        "ResultValidationError",
        "PUBLIC_SCHEMA_VERSION",
    ],
)
def test_matching_version_with_incomplete_api_is_rejected(contract_project, attribute):
    root, api = contract_project
    delattr(api, attribute)
    checks = contract_check.check_contract(root)
    assert checks[0]["status"] == "pass"
    assert checks[1]["status"] == "fail"


@pytest.mark.parametrize(
    "content",
    [
        "invalid TOML",
        "[project]\ndependencies = []",
        '[project]\ndependencies = ["audio-transcribe-contract???"]',
    ],
)
def test_invalid_declaration_is_rejected(contract_project, content):
    root, _ = contract_project
    (root / "pyproject.toml").write_text(content, encoding="utf-8")
    assert contract_check.check_contract(root)[0]["status"] == "fail"


def test_missing_declaration_is_rejected(contract_project):
    root, _ = contract_project
    (root / "pyproject.toml").unlink()
    assert contract_check.check_contract(root)[0]["status"] == "fail"


def test_checker_cannot_report_ready_when_contract_fails(monkeypatch):
    from scripts import check_dependencies

    monkeypatch.setattr(
        check_dependencies,
        "check_contract",
        lambda root: [
            {
                "id": "contract:version",
                "status": "fail",
                "expected": "0.3.0",
                "actual": "0.1.1",
                "message": "version mismatch",
                "fix": "sync",
            }
        ],
    )
    report = check_dependencies.run_check(Path(__file__).resolve().parents[1])
    assert report["overall_status"] == "not_ready"
