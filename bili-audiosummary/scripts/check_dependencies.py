from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SKILL_NAME = "bili-audiosummary"


def _item(
    check_id: str, status: str, expected: str, actual: str, message: str, fix: str = ""
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


def _ffmpeg_checks() -> list[dict[str, str]]:
    try:
        import ffmpeg_binaries as ffmpeg

        ffmpeg.init()
        location = Path(str(ffmpeg.FFMPEG_PATH))
        directory = location.parent if location.is_file() else location
        paths = [directory / "ffmpeg.exe", directory / "ffprobe.exe"]
    except Exception as exc:
        return [
            _item(
                "ffmpeg-binaries",
                "fail",
                "ffmpeg_binaries provides two executables",
                "unavailable",
                str(exc),
                "uv sync --python 3.12",
            )
        ]
    results = [
        _item(
            "ffmpeg-binaries",
            "pass",
            "ffmpeg_binaries provides two executables",
            str(directory),
            "Packaged binary directory resolved.",
        )
    ]
    for name, path in zip(("ffmpeg", "ffprobe"), paths, strict=True):
        if not path.is_file():
            results.append(
                _item(
                    name,
                    "fail",
                    "packaged executable",
                    str(path),
                    "Executable is missing.",
                    "uv sync --python 3.12",
                )
            )
            continue
        code, stdout, stderr = _run([str(path), "-version"])
        results.append(
            _item(
                name,
                "pass" if code == 0 else "fail",
                "-version exits 0",
                str(path),
                (stdout or stderr)[:300],
                "uv sync --python 3.12" if code else "",
            )
        )
    return results


def run_check(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    now = datetime.now(timezone.utc).isoformat()
    items: list[dict[str, str]] = []
    items.append(
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
        exists = (root / filename).is_file()
        items.append(
            _item(
                filename,
                "pass" if exists else "fail",
                "file exists",
                str(root / filename),
                "Project configuration found."
                if exists
                else "Project configuration is missing.",
                "Restore the project file.",
            )
        )
    uv = shutil.which("uv")
    if uv:
        code, out, err = _run([uv, "--version"])
        items.append(
            _item(
                "uv",
                "pass" if code == 0 else "fail",
                "uv is executable",
                uv,
                (out or err)[:300],
                "Install uv and reopen PowerShell.",
            )
        )
        uv_version = out or err
    else:
        items.append(
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
    python_ok = sys.version_info[:2] == (3, 12)
    items.append(
        _item(
            "python",
            "pass" if python_ok else "fail",
            "3.12.x",
            version,
            f"Checker interpreter: {python}",
            "Use the Skill .venv Python 3.12.",
        )
    )
    if uv and (root / "uv.lock").is_file():
        code, out, err = _run([uv, "pip", "check", "--python", str(python)])
        items.append(
            _item(
                "uv-pip-check",
                "pass" if code == 0 else "fail",
                "dependencies match environment",
                (out or err)[:300],
                "uv pip check completed."
                if code == 0
                else "Declared dependencies are missing or inconsistent.",
                "uv sync --python 3.12",
            )
        )
    else:
        items.append(
            _item(
                "uv-pip-check",
                "fail",
                "dependencies match environment",
                "not run",
                "Cannot validate the environment.",
                "Restore uv, uv.lock and the .venv.",
            )
        )
    for module in ("yt_dlp", "ffmpeg_binaries", "audio_transcribe_contract"):
        try:
            imported = __import__(module)
            value = getattr(imported, "__version__", "importable")
            items.append(
                _item(
                    f"import:{module}",
                    "pass",
                    "module imports",
                    str(value),
                    "Module imported successfully.",
                )
            )
        except Exception as exc:
            items.append(
                _item(
                    f"import:{module}",
                    "fail",
                    "module imports",
                    type(exc).__name__,
                    str(exc),
                    "uv sync --python 3.12",
                )
            )
    items.extend(_ffmpeg_checks())
    items.append(
        _item(
            "audio-transcribe",
            "warn",
            "external Skill checked separately",
            "not checked",
            "bili-audiosummary does not import or locate audio-transcribe.",
            "Run audio-transcribe\\scripts\\check_dependencies.bat before external transcription.",
        )
    )
    failed = [item for item in items if item["status"] == "fail"]
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "checked_at": now,
        "root": str(root),
        "windows": os.name == "nt",
        "python": {"path": str(python), "version": version},
        "uv": {"path": uv or "", "version": uv_version},
        "overall_status": "not_ready" if failed else "ready",
        "checks": items,
        "providers": {},
        "logs": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only dependency check for bili-audiosummary."
    )
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_check(args.root)
    logs_dir = (
        (args.root or Path(__file__).resolve().parents[1]).resolve() / ".cache" / "logs"
    )
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    json_path = logs_dir / f"dependency-check-{stamp}.json"
    log_path = logs_dir / f"dependency-check-{stamp}.log"
    report["logs"] = {"report": str(json_path), "log": str(log_path)}
    temporary = json_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, json_path)
    lines = [
        f"Dependency check: {report['skill']}",
        f"Overall: {report['overall_status']}",
    ]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper()}] {check['id']}: {check['message']}")
    lines.extend([f"JSON report: {json_path}", f"Log: {log_path}"])
    log_temporary = log_path.with_suffix(".tmp")
    log_temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(log_temporary, log_path)
    print("\n".join(lines))
    return (
        2
        if any(
            item["id"] in {"platform", "pyproject.toml", "uv.lock", "uv"}
            and item["status"] == "fail"
            for item in report["checks"]
        )
        else (1 if report["overall_status"] == "not_ready" else 0)
    )


if __name__ == "__main__":
    raise SystemExit(main())
