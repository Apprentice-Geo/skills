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

from scripts.config import (
    DEFAULT_WHISPER_MODEL_DIR,
    LANGUAGE_ID_MODEL_DIR,
    QWEN3_ALIGNER_MODEL_DIR,
    QWEN3_ASR_MODEL_DIR,
)
from scripts.model_artifacts import (
    LANGUAGE_ID_REQUIRED_FILES,
    QWEN3_WEIGHT_PATTERNS,
    WHISPER_WEIGHT_PATTERNS,
)
from scripts.model_identity import MODEL_REVISIONS

SCHEMA_VERSION = 1
SKILL_NAME = "audio-transcribe"


def item(
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


def run_command(command: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return (
            result.returncode,
            (result.stdout or "").strip(),
            (result.stderr or "").strip(),
        )
    except OSError as exc:
        return 1, "", str(exc)


def check_module_import(module: str) -> tuple[bool, str, str]:
    """Import one dependency in a clean interpreter process."""
    statement = (
        "import importlib; "
        f"module = importlib.import_module({module!r}); "
        "print(getattr(module, '__version__', 'importable'))"
    )
    code, stdout, stderr = run_command([sys.executable, "-c", statement])
    return code == 0, (stdout or stderr)[:300], stderr[:300]


def model_check(
    check_id: str,
    directory: Path,
    patterns: tuple[str, ...],
    identity: dict[str, str],
    required: tuple[str, ...] = (),
) -> dict[str, str]:
    missing = [name for name in required if not (directory / name).is_file()]
    weights = (
        any(path.is_file() for pattern in patterns for path in directory.glob(pattern))
        if patterns
        else directory.is_dir()
    )
    try:
        marker = json.loads(
            (directory / ".model_identity.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError):
        marker = None
    correct_marker = marker == identity
    status = (
        "pass"
        if directory.is_dir() and weights and not missing and correct_marker
        else "fail"
    )
    actual = f"{directory}; weights={'yes' if weights else 'no'}; marker={'match' if correct_marker else 'missing/mismatch'}"
    if missing:
        actual += f"; missing={','.join(missing)}"
    return item(
        check_id,
        status,
        f"weights and revision {identity['revision']}",
        actual,
        "Model artifacts are ready."
        if status == "pass"
        else "Model directory is incomplete or has the wrong revision marker.",
        f"uv run --no-sync python -m scripts.setup.install_model --model {'qwen3' if 'qwen3' in check_id else 'faster-whisper'}",
    )


def ffmpeg_checks() -> list[dict[str, str]]:
    try:
        import ffmpeg_binaries as ffmpeg

        ffmpeg.init()
        location = Path(str(ffmpeg.FFMPEG_PATH))
        directory = location.parent if location.is_file() else location
    except Exception as exc:
        return [
            item(
                "ffmpeg-binaries",
                "fail",
                "packaged ffmpeg and ffprobe",
                "unavailable",
                str(exc),
                "uv sync --python 3.12",
            )
        ]
    results = [
        item(
            "ffmpeg-binaries",
            "pass",
            "packaged ffmpeg and ffprobe",
            str(directory),
            "Packaged binary directory resolved.",
        )
    ]
    for name in ("ffmpeg", "ffprobe"):
        path = directory / f"{name}.exe"
        if not path.is_file():
            results.append(
                item(
                    name,
                    "fail",
                    "packaged executable",
                    str(path),
                    "Executable is missing.",
                    "uv sync --python 3.12",
                )
            )
            continue
        code, out, err = run_command([str(path), "-version"])
        results.append(
            item(
                name,
                "pass" if code == 0 else "fail",
                "-version exits 0",
                str(path),
                (out or err)[:300],
                "uv sync --python 3.12" if code else "",
            )
        )
    return results


def run_check(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    checks: list[dict[str, str]] = []
    checks.append(
        item(
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
        checks.append(
            item(
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
        code, out, err = run_command([uv, "--version"])
        checks.append(
            item(
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
        checks.append(
            item(
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
    checks.append(
        item(
            "python",
            "pass" if python_ok else "fail",
            "3.12.x",
            version,
            f"Checker interpreter: {python}",
            "Use the Skill .venv Python 3.12.",
        )
    )
    if uv and (root / "uv.lock").is_file():
        code, out, err = run_command([uv, "pip", "check", "--python", str(python)])
        checks.append(
            item(
                "uv-pip-check",
                "pass" if code == 0 else "fail",
                "dependencies match environment",
                (out or err)[:300],
                "Dependencies are consistent."
                if code == 0
                else "Dependencies are missing or inconsistent.",
                "uv sync --python 3.12",
            )
        )
    else:
        checks.append(
            item(
                "uv-pip-check",
                "fail",
                "dependencies match environment",
                "not run",
                "Cannot validate the environment.",
                "Restore uv, uv.lock and the .venv.",
            )
        )

    import_status: dict[str, bool] = {}
    for module in (
        "audio_transcribe_contract",
        "faster_whisper",
        "ffmpeg_binaries",
        "numpy",
        "psutil",
        "speechbrain",
        "torch",
        "torchaudio",
    ):
        imported_ok, actual, stderr = check_module_import(module)
        if imported_ok:
            import_status[module] = True
            checks.append(
                item(
                    f"import:{module}",
                    "pass",
                    "module imports",
                    actual,
                    "Module imported successfully.",
                )
            )
        else:
            import_status[module] = False
            checks.append(
                item(
                    f"import:{module}",
                    "fail",
                    "module imports",
                    actual or "import failed",
                    stderr or actual,
                    "uv sync --python 3.12",
                )
            )
    checks.extend(ffmpeg_checks())
    language = model_check(
        "model:language-id",
        LANGUAGE_ID_MODEL_DIR,
        (),
        {
            "repo": MODEL_REVISIONS["language-id"]["repo"],
            "revision": MODEL_REVISIONS["language-id"]["revision"],
        },
        LANGUAGE_ID_REQUIRED_FILES,
    )
    whisper = model_check(
        "provider:faster-whisper:model",
        DEFAULT_WHISPER_MODEL_DIR,
        WHISPER_WEIGHT_PATTERNS,
        {
            "repo": MODEL_REVISIONS["faster-whisper"]["repo"],
            "revision": MODEL_REVISIONS["faster-whisper"]["revision"],
        },
    )
    checks.extend((language, whisper))
    qwen_asr = model_check(
        "provider:qwen3-asr:asr-model",
        QWEN3_ASR_MODEL_DIR,
        QWEN3_WEIGHT_PATTERNS,
        {
            "repo": MODEL_REVISIONS["qwen3"]["repo"],
            "revision": MODEL_REVISIONS["qwen3"]["revision"],
        },
    )
    qwen_aligner = model_check(
        "provider:qwen3-asr:forced-aligner",
        QWEN3_ALIGNER_MODEL_DIR,
        QWEN3_WEIGHT_PATTERNS,
        {
            "repo": MODEL_REVISIONS["qwen3"]["aligner_repo"],
            "revision": MODEL_REVISIONS["qwen3"]["aligner_revision"],
        },
    )
    checks.extend((qwen_asr, qwen_aligner))
    qwen_optional_imports: dict[str, bool] = {}
    for module in ("qwen_asr", "transformers"):
        imported_ok, actual, stderr = check_module_import(module)
        if imported_ok:
            qwen_optional_imports[module] = True
        else:
            qwen_optional_imports[module] = False
            checks.append(
                item(
                    f"import:{module}",
                    "warn",
                    "Qwen3 optional module imports",
                    actual or "import failed",
                    stderr or actual,
                    "uv sync --python 3.12 --no-dev --extra qwen3",
                )
            )
    qwen_imports = all(
        import_status.get(name, False) for name in ("torch", "torchaudio")
    ) and all(qwen_optional_imports.values())
    cuda = False
    if import_status.get("torch"):
        try:
            import torch

            cuda = bool(torch.cuda.is_available())
        except Exception:
            pass
    checks.append(
        item(
            "provider:qwen3-asr:cuda",
            "pass" if cuda else "warn",
            "CUDA is available",
            str(cuda),
            "CUDA is available."
            if cuda
            else "Qwen3 requires a CUDA GPU; faster-whisper can run on CPU.",
            "Install a CUDA-enabled PyTorch build and GPU driver.",
        )
    )
    checks.append(
        item(
            "provider:qwen3-asr:imports",
            "pass" if qwen_imports else "warn",
            "Qwen3 optional imports",
            "available" if qwen_imports else "missing",
            "Qwen3 imports are available."
            if qwen_imports
            else "Qwen3 optional dependencies are not installed.",
            "uv sync --python 3.12 --no-dev --extra qwen3",
        )
    )
    ffmpeg_ok = all(
        check["status"] == "pass"
        for check in checks
        if check["id"] in {"ffmpeg", "ffprobe", "ffmpeg-binaries"}
    )
    whisper_ready = (
        all(
            import_status.get(name, False)
            for name in ("faster_whisper", "ffmpeg_binaries")
        )
        and ffmpeg_ok
        and language["status"] == "pass"
        and whisper["status"] == "pass"
    )
    qwen_ready = (
        qwen_imports
        and cuda
        and ffmpeg_ok
        and language["status"] == "pass"
        and qwen_asr["status"] == "pass"
        and qwen_aligner["status"] == "pass"
    )
    providers = {
        "faster-whisper": {"status": "ready" if whisper_ready else "not_ready"},
        "qwen3-asr": {"status": "ready" if qwen_ready else "not_ready"},
    }
    core_ids = {
        "platform",
        "pyproject.toml",
        "uv.lock",
        "uv",
        "python",
        "uv-pip-check",
        "ffmpeg-binaries",
        "ffmpeg",
        "ffprobe",
    } | {
        f"import:{name}"
        for name in (
            "audio_transcribe_contract",
            "faster_whisper",
            "ffmpeg_binaries",
            "numpy",
            "psutil",
            "speechbrain",
            "torch",
            "torchaudio",
        )
    }
    core_failed = any(
        check["id"] in core_ids and check["status"] == "fail" for check in checks
    )
    ready_count = sum(value["status"] == "ready" for value in providers.values())
    overall = (
        "not_ready"
        if core_failed or ready_count == 0
        else ("ready" if ready_count == 2 else "degraded")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": SKILL_NAME,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "windows": os.name == "nt",
        "python": {"path": str(python), "version": version},
        "uv": {"path": uv or "", "version": uv_version},
        "overall_status": overall,
        "checks": checks,
        "providers": providers,
        "logs": {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only dependency check for audio-transcribe."
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
    lines.extend(
        f"[{check['status'].upper()}] {check['id']}: {check['message']}"
        for check in report["checks"]
    )
    lines.extend(
        [
            f"Provider {name}: {value['status']}"
            for name, value in report["providers"].items()
        ]
    )
    lines.extend([f"JSON report: {json_path}", f"Log: {log_path}"])
    log_temporary = log_path.with_suffix(".tmp")
    log_temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(log_temporary, log_path)
    print("\n".join(lines))
    if any(
        check["id"] in {"platform", "pyproject.toml", "uv.lock", "uv"}
        and check["status"] == "fail"
        for check in report["checks"]
    ):
        return 2
    return 1 if report["overall_status"] == "not_ready" else 0


if __name__ == "__main__":
    raise SystemExit(main())
