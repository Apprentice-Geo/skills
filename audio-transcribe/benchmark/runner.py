from __future__ import annotations

import argparse
import ctypes
import os
import platform
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from benchmark import (
    DATA_DIR,
    DEFAULT_REPETITIONS,
    LANGUAGES,
    MINUTES,
    MODES,
    PROVIDERS,
    REFERENCE_MANIFEST,
    ROOT,
)
from benchmark.metrics import (
    COMPARISON_POLICY,
    compare_reference,
    compare_text,
    normalize_text,
    unit_digest,
)
from benchmark.reference import (
    freeze_reference_set,
    load_reference_manifest,
    load_reference_samples,
)
from benchmark.report import run_id, validate_config, validate_report, write_report
from benchmark.worker import WorkerSession, fresh_worker_directory
from scripts.io_utils import read_json
from scripts.model_identity import MODEL_REVISIONS


def build_matrix(
    providers: Iterable[str] = PROVIDERS,
    languages: Iterable[str] = LANGUAGES,
    minutes: Iterable[int] = MINUTES,
    modes: Iterable[str] = MODES,
    repetitions: int = DEFAULT_REPETITIONS,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    selected_modes = tuple(modes)
    for provider in providers:
        for language in languages:
            for minute in minutes:
                for repetition in range(1, repetitions + 1):
                    order = MODES if repetition % 2 else tuple(reversed(MODES))
                    runs.extend(
                        {
                            "provider": provider,
                            "language": language,
                            "minutes": minute,
                            "mode": mode,
                            "repetition": repetition,
                        }
                        for mode in order
                        if mode in selected_modes
                    )
    return runs


def _command(*args: str) -> str | None:
    try:
        return subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _cpu_model() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "ProcessorNameString")
                if str(value).strip():
                    return str(value).strip()
        except OSError:
            pass
    return (platform.processor() or platform.machine()).strip()


def _physical_memory_bytes() -> int:
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return int(status.total_physical)
    page_size = os.sysconf("SC_PAGE_SIZE")
    page_count = os.sysconf("SC_PHYS_PAGES")
    return int(page_size * page_count)


def _gpus() -> list[dict[str, Any]]:
    try:
        output = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except FileNotFoundError:
        return []
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Could not collect NVIDIA GPU identity") from exc
    if not output:
        return []
    devices = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",", 2)]
        if len(fields) != 3:
            raise RuntimeError("nvidia-smi returned an invalid GPU identity")
        index, name, memory_mib = fields
        devices.append(
            {
                "index": int(index),
                "name": name,
                "memory_total_bytes": int(memory_mib) * 1024 * 1024,
            }
        )
    return sorted(devices, key=lambda item: item["index"])


def hardware_identity() -> dict[str, Any]:
    identity = {
        "cpu_model": _cpu_model(),
        "logical_cpu_count": os.cpu_count(),
        "physical_memory_bytes": _physical_memory_bytes(),
        "gpus": _gpus(),
    }
    if (
        not identity["cpu_model"]
        or not isinstance(identity["logical_cpu_count"], int)
        or identity["logical_cpu_count"] < 1
        or identity["physical_memory_bytes"] < 1
    ):
        raise RuntimeError("Required benchmark hardware identity is unavailable")
    return identity


def environment() -> dict[str, Any]:
    return {
        "hardware": hardware_identity(),
        "audit": {
            "platform": platform.platform(),
            "python": sys.version,
            "commit": _command("git", "rev-parse", "HEAD"),
            "dependencies": _command(sys.executable, "-m", "pip", "freeze"),
            "model_revisions": MODEL_REVISIONS,
        },
    }


def _canonical(items: list[Any], allowed: tuple[Any, ...], name: str) -> list[Any]:
    if any(item not in allowed for item in items):
        raise ValueError(f"Invalid benchmark {name}")
    return [item for item in allowed if item in items]


def resolve_config(
    args: argparse.Namespace, saved: dict[str, Any] | None = None
) -> dict[str, Any]:
    if saved is not None:
        saved = validate_config(saved)
    values: dict[str, Any] = {}
    for argument, field, allowed in (
        ("provider", "providers", PROVIDERS),
        ("language", "languages", LANGUAGES),
        ("minutes", "minutes", MINUTES),
        ("mode", "modes", MODES),
    ):
        supplied = getattr(args, argument, None)
        values[field] = (
            list(saved[field])
            if supplied is None and saved is not None
            else _canonical(list(supplied or allowed), allowed, argument)
        )
    repetitions = getattr(args, "repetitions", None)
    values["repetitions"] = (
        saved["repetitions"]
        if repetitions is None and saved is not None
        else DEFAULT_REPETITIONS
        if repetitions is None
        else repetitions
    )
    config = validate_config(values)
    if saved is not None and config != saved:
        raise ValueError(
            "Benchmark config differs from the existing report; use a new report path."
        )
    return config


def _load_references(
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[tuple[str, int], dict[str, Any]]]:
    manifest = load_reference_manifest(REFERENCE_MANIFEST)
    keys = {
        (language, minute)
        for language in config["languages"]
        for minute in config["minutes"]
    }
    references = load_reference_samples(
        manifest,
        keys,
        data_dir=DATA_DIR,
        samples_manifest_path=DATA_DIR / "samples.json",
        normalize_units=normalize_text,
        unit_digest=unit_digest,
    )
    return manifest, references


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    report_path: Path = args.report
    if report_path.exists():
        candidate = read_json(report_path)
        if not isinstance(candidate, dict) or set(candidate) != {
            "config",
            "comparison_policy",
            "reference_set",
            "environment",
            "warmups",
            "runs",
        }:
            raise ValueError(
                "Benchmark report structure is invalid; use a new report path."
            )
        config = resolve_config(args, candidate.get("config"))
        manifest, references = _load_references(config)
        expected_reference_set = freeze_reference_set(manifest, references)
        if candidate.get("reference_set") != expected_reference_set:
            raise ValueError(
                "Benchmark reference identity changed; use a new report path."
            )
        report = validate_report(candidate, references)
        if report["environment"]["hardware"] != hardware_identity():
            raise ValueError(
                "Benchmark hardware differs from the existing report; "
                "use a new report path."
            )
        print(
            "Benchmark hardware matches. Use a new report path if code, dependencies, "
            "or models changed.",
            file=sys.stderr,
        )
    else:
        config = resolve_config(args)
        manifest, references = _load_references(config)
        report = {
            "config": config,
            "comparison_policy": COMPARISON_POLICY,
            "reference_set": freeze_reference_set(manifest, references),
            "environment": environment(),
            "warmups": [],
            "runs": [],
        }

    matrix = build_matrix(
        config["providers"],
        config["languages"],
        config["minutes"],
        config["modes"],
        config["repetitions"],
    )
    succeeded = {
        item["run_id"] for item in report["runs"] if item.get("status") == "succeeded"
    }
    for provider in config["providers"]:
        provider_runs = [
            item
            for item in matrix
            if item["provider"] == provider and run_id(item) not in succeeded
        ]
        if not provider_runs:
            continue
        session: WorkerSession | None = None
        try:
            for run in provider_runs:
                if session is None or not session.alive:
                    if session is not None:
                        session.close()
                    session = WorkerSession(provider)
                identifier = run_id(run)
                attempt = 1 + sum(
                    item["run_id"] == identifier for item in report["runs"]
                )
                reference = references[(run["language"], run["minutes"])]
                sample = DATA_DIR / f"{run['language']}-{run['minutes']}min.wav"
                warmup = session.ensure_warmup(
                    run,
                    sample,
                    fresh_worker_directory(
                        f"warmup-{provider}-{len(report['warmups']) + 1}"
                    ),
                )
                if not warmup.pop("already_prepared", False):
                    report["warmups"].append(warmup)
                    write_report(report_path, report)
                if warmup.get("status") == "succeeded":
                    result = session.run(
                        run,
                        sample,
                        fresh_worker_directory(f"{identifier}-attempt-{attempt}"),
                    )
                else:
                    result = {
                        **run,
                        "run_id": identifier,
                        "session_id": session.session_id,
                        "status": "failed",
                        "error": f"warmup failed: {warmup.get('error', 'unknown error')}",
                    }
                result["attempt"] = attempt
                result["audio_sha256"] = reference["audio_sha256"]
                if result.get("status") == "succeeded":
                    result["reference_comparison"] = compare_reference(
                        result["text"], reference["text"], run["language"]
                    )
                    succeeded.add(identifier)
                report["runs"].append(result)
                counterpart = next(
                    (
                        item
                        for item in report["runs"]
                        if item.get("status") == "succeeded"
                        and item["provider"] == run["provider"]
                        and item["language"] == run["language"]
                        and item["minutes"] == run["minutes"]
                        and item["repetition"] == run["repetition"]
                        and item["mode"] != run["mode"]
                    ),
                    None,
                )
                if result.get("status") == "succeeded" and counterpart is not None:
                    project = (
                        result if result["mode"] == "project-slicing" else counterpart
                    )
                    native = (
                        result if result["mode"] == "provider-native" else counterpart
                    )
                    project["output_comparison"] = compare_text(
                        project["text"], native["text"], run["language"]
                    )
                write_report(report_path, report)
                if not session.alive:
                    session.close()
                    session = None
        finally:
            if session is not None:
                session.close()
    write_report(report_path, report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare project slicing with provider-native transcription."
    )
    parser.add_argument("--provider", action="append", choices=PROVIDERS)
    parser.add_argument("--language", action="append", choices=LANGUAGES)
    parser.add_argument("--minutes", action="append", type=int, choices=MINUTES)
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "benchmark" / "reports" / f"{date.today().isoformat()}.json",
    )
    args = parser.parse_args(argv)
    if args.repetitions is not None and args.repetitions < 1:
        parser.error("--repetitions must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    run_benchmark(parse_args(argv))
    return 0
