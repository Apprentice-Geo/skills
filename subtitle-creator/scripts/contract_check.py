from __future__ import annotations

import importlib
import json
import tomllib
from importlib import metadata
from pathlib import Path


def check_contract(root: Path) -> list[dict[str, str]]:
    expected, actual = "valid project dependency", "unavailable"
    fix = "uv sync --python 3.12; rerun the dependency checker"
    try:
        from packaging.requirements import Requirement
        from packaging.version import Version

        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = [Requirement(value) for value in project["project"]["dependencies"]]
        matches = [
            r
            for r in requirements
            if r.name.lower().replace("_", "-") == "audio-transcribe-contract"
        ]
        if len(matches) != 1 or not matches[0].specifier or matches[0].url or matches[0].marker:
            raise ValueError(
                "Expected one unambiguous version requirement for audio-transcribe-contract"
            )
        requirement = matches[0]
        expected = str(requirement)
        actual = metadata.version("audio-transcribe-contract")
        if Version(actual) not in requirement.specifier:
            raise ValueError("Installed distribution does not satisfy the project requirement")
    except (
        ImportError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        metadata.PackageNotFoundError,
    ) as exc:
        version_status, version_message = "fail", f"{type(exc).__name__}: {exc}"
    else:
        version_status, version_message = (
            "pass",
            "Installed contract matches the project requirement.",
        )
    checks = [
        {
            "id": "contract:version",
            "status": version_status,
            "expected": expected,
            "actual": actual,
            "message": version_message,
            "fix": fix if version_status == "fail" else "",
        }
    ]
    try:
        contract = importlib.import_module("audio_transcribe_contract")
        if not callable(getattr(contract, "load_result", None)):
            raise TypeError("Missing callable load_result")
        if not isinstance(getattr(contract, "TranscriptionResult", None), type):
            raise TypeError("Missing TranscriptionResult type")
        error_type = getattr(contract, "ResultValidationError", None)
        if not isinstance(error_type, type) or not issubclass(error_type, Exception):
            raise TypeError("Missing ResultValidationError exception type")
        if (
            type(getattr(contract, "PUBLIC_SCHEMA_VERSION", None)) is not int
            or contract.PUBLIC_SCHEMA_VERSION != 3
        ):
            raise ValueError("Unsupported PUBLIC_SCHEMA_VERSION; consumer requires public schema 3")
    except Exception as exc:  # noqa: BLE001 - 将公共包导入故障转成失败检查项
        api_status, api_message = "fail", f"{type(exc).__name__}: {exc}"
    else:
        api_status, api_message = "pass", "Public contract capabilities are available."
    checks.append(
        {
            "id": "contract:api",
            "status": api_status,
            "expected": "load_result, TranscriptionResult, ResultValidationError, PUBLIC_SCHEMA_VERSION=3",
            "actual": api_message,
            "message": api_message,
            "fix": fix if api_status == "fail" else "",
        }
    )
    return checks


def main() -> int:
    checks = check_contract(Path(__file__).resolve().parents[1])
    print(json.dumps(checks, ensure_ascii=False))
    return int(any(check["status"] == "fail" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
