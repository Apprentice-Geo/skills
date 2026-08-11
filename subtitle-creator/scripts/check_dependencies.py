from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SKILL_NAME = "subtitle-creator"


def _item(
    check_id: str,
    status: str,
    expected: str,
    actual: str,
    message: str,
    fix: str = "",
) -> dict[str, str]:
    return {
        "id": check_id,
        "status": status,
        "expected": expected,
        "actual": actual,
        "message": message,
        "fix": fix,
    }


def _run(command: list[str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return (
            completed.returncode,
            (completed.stdout or "").strip(),
            (completed.stderr or "").strip(),
        )
    except OSError as exc:
        return 1, "", str(exc)


def run_check(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    checks: list[dict[str, str]] = []
    setup_command = r".\scripts\setup\setup_windows.bat"

    checks.append(
        _item(
            "platform",
            "pass" if os.name == "nt" else "fail",
            "Windows",
            platform.platform(),
            "Platform check.",
            "Run this checker on Windows.",
        )
    )
    for filename in ("pyproject.toml", "uv.lock"):
        path = root / filename
        checks.append(
            _item(
                filename,
                "pass" if path.is_file() else "fail",
                "file exists",
                str(path),
                "Project configuration found."
                if path.is_file()
                else "Project configuration is missing.",
                "Restore the project file.",
            )
        )

    uv = shutil.which("uv")
    if uv:
        code, stdout, stderr = _run([uv, "--version"])
        checks.append(
            _item(
                "uv",
                "pass" if code == 0 else "fail",
                "uv is executable",
                uv,
                (stdout or stderr)[:300],
                "Install uv and reopen PowerShell.",
            )
        )
        uv_version = stdout or stderr
    else:
        checks.append(
            _item(
                "uv",
                "fail",
                "uv is executable",
                "not found",
                "uv was not found on PATH.",
                "Install uv from https://docs.astral.sh/uv/",
            )
        )
        uv_version = ""

    python = Path(sys.executable).resolve()
    version = platform.python_version()
    checks.append(
        _item(
            "python",
            "pass" if sys.version_info[:2] == (3, 12) else "fail",
            "3.12.x",
            version,
            f"Checker interpreter: {python}",
            setup_command,
        )
    )

    if uv and (root / "uv.lock").is_file():
        code, stdout, stderr = _run([uv, "pip", "check", "--python", str(python)])
        checks.append(
            _item(
                "uv-pip-check",
                "pass" if code == 0 else "fail",
                "dependencies match environment",
                (stdout or stderr)[:300],
                "uv pip check completed."
                if code == 0
                else "Declared dependencies are missing or inconsistent.",
                setup_command,
            )
        )
    else:
        checks.append(
            _item(
                "uv-pip-check",
                "fail",
                "dependencies match environment",
                "not run",
                "Cannot validate the environment.",
                setup_command,
            )
        )

    code, stdout, stderr = _run(
        [
            str(python),
            "-c",
            "import audio_transcribe_contract as c; print(getattr(c, '__version__', 'importable'))",
        ]
    )
    checks.append(
        _item(
            "import:audio_transcribe_contract",
            "pass" if code == 0 else "fail",
            "module imports",
            (stdout or stderr)[:300],
            "Public transcription contract imported successfully."
            if code == 0
            else "Public transcription contract import failed.",
            "" if code == 0 else setup_command,
        )
    )

    checks.append(
        _item(
            "audio-transcribe",
            "warn",
            "external Skill checked separately",
            "not checked",
            "subtitle-creator does not locate or configure audio-transcribe.",
            r"Run audio-transcribe\scripts\check_dependencies.bat before transcription.",
        )
    )
    failed = [item for item in checks if item["status"] == "fail"]
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "checked_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "windows": os.name == "nt",
        "python": {"path": str(python), "version": version},
        "uv": {"path": uv or "", "version": uv_version},
        "overall_status": "not_ready" if failed else "ready",
        "checks": checks,
        "logs": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only dependency check for subtitle-creator.")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_check(args.root)
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    logs_dir = root / ".cache" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    json_path = logs_dir / f"dependency-check-{stamp}.json"
    log_path = logs_dir / f"dependency-check-{stamp}.log"
    report["logs"] = {"report": str(json_path), "log": str(log_path)}

    temporary = json_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, json_path)

    check_lines = [
        f"[{check['status'].upper()}] {check['id']}: {check['message']}"
        for check in report["checks"]
    ]
    terminal_lines = [
        f"Dependency check: {report['skill']}",
        f"Overall: {report['overall_status']}",
    ]
    passed = sum(check["status"] == "pass" for check in report["checks"])
    if passed:
        terminal_lines.append(f"[PASS] Dependencies OK ({passed} checks passed)")
    terminal_lines.extend(line for line in check_lines if not line.startswith("[PASS] "))
    terminal_lines.extend([f"JSON report: {json_path}", f"Log: {log_path}"])

    log_temporary = log_path.with_suffix(".tmp")
    log_temporary.write_text(
        "\n".join(
            [
                f"Dependency check: {report['skill']}",
                f"Overall: {report['overall_status']}",
                *check_lines,
                f"JSON report: {json_path}",
                f"Log: {log_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(log_temporary, log_path)
    print("\n".join(terminal_lines))

    critical_ids = {"platform", "pyproject.toml", "uv.lock", "uv"}
    if any(item["id"] in critical_ids and item["status"] == "fail" for item in report["checks"]):
        return 2
    return 1 if report["overall_status"] == "not_ready" else 0


if __name__ == "__main__":
    raise SystemExit(main())
