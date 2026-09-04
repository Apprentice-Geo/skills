from __future__ import annotations

import argparse
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
from benchmark.worker import fresh_worker_directory, run_worker
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


def environment() -> dict[str, Any]:
    def command(*args: str) -> str | None:
        try:
            return subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    return {
        "platform": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "cpu_model": command("wmic", "cpu", "get", "name", "/value"),
        "gpu_model": command("nvidia-smi", "--query-gpu=name", "--format=csv,noheader"),
        "commit": command("git", "rev-parse", "HEAD"),
        "dependencies": command(sys.executable, "-m", "pip", "freeze"),
        "model_revisions": MODEL_REVISIONS,
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
        print(
            "Resuming assumes code, dependencies, models, and machine are unchanged; "
            "use a new report path if they changed.",
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
    warmed = {
        item["provider"]
        for item in report["warmups"]
        if item.get("status") == "succeeded"
    }
    for provider in config["providers"]:
        provider_runs = [item for item in matrix if item["provider"] == provider]
        if provider not in warmed:
            warm = {
                "provider": provider,
                "language": config["languages"][0],
                "minutes": config["minutes"][0],
                "mode": "project-slicing",
                "repetition": 0,
            }
            result = run_worker(
                warm,
                DATA_DIR / f"{warm['language']}-{warm['minutes']}min.wav",
                fresh_worker_directory(
                    f"warmup-{provider}-{len(report['warmups']) + 1}"
                ),
            )
            report["warmups"].append(result)
            write_report(report_path, report)
        for run in provider_runs:
            identifier = run_id(run)
            if identifier in succeeded:
                continue
            attempt = 1 + sum(item["run_id"] == identifier for item in report["runs"])
            reference = references[(run["language"], run["minutes"])]
            result = run_worker(
                run,
                DATA_DIR / f"{run['language']}-{run['minutes']}min.wav",
                fresh_worker_directory(f"{identifier}-attempt-{attempt}"),
            )
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
                project = result if result["mode"] == "project-slicing" else counterpart
                native = result if result["mode"] == "provider-native" else counterpart
                project["output_comparison"] = compare_text(
                    project["text"], native["text"], run["language"]
                )
            write_report(report_path, report)
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
