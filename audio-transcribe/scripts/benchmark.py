from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import uuid
import wave
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import psutil

from benchmark.reference import (
    freeze_reference_set,
    load_reference_manifest,
    load_reference_samples,
)
from scripts.io_utils import read_json, write_json_atomic
from scripts.model_identity import MODEL_REVISIONS
from scripts.text_normalization import (
    TEXT_NORMALIZATION_POLICY,
    normalize_transcript_text,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "benchmark" / "data"
REFERENCE_MANIFEST = ROOT / "benchmark" / "references" / "manifest.json"
TMP_DIR = ROOT / "benchmark" / "tmp"
MODES = ("project-slicing", "provider-native")
PROVIDERS = ("faster-whisper", "qwen3-asr")
LANGUAGES = ("zh", "en")
MINUTES = (8, 16, 32, 64)
REPORT_SCHEMA_VERSION = 3
COMPARISON_POLICY = {
    "id": "benchmark-reference-v1",
    "text_normalization": TEXT_NORMALIZATION_POLICY,
    "zh_units": "remove Unicode whitespace and punctuation after NFKC and OpenCC t2s",
    "en_units": "Unicode words after NFKC and casefold; preserve internal ' and ’",
    "punctuation": "count Unicode punctuation after language normalization",
    "mode_pairing": "pair project-slicing and provider-native by repetition; provider-native is the denominator",
}


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16_000:
            raise ValueError(f"Benchmark sample must be mono 16 kHz PCM WAV: {path}")
        return stream.getnframes() / stream.getframerate()


def normalize_text(text: str, language: str) -> list[str]:
    text = normalize_transcript_text(text, language)
    if language == "zh":
        return [
            char
            for char in text
            if not char.isspace() and not unicodedata.category(char).startswith("P")
        ]
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold(), re.UNICODE)


def punctuation_count(text: str, language: str) -> int:
    return sum(
        unicodedata.category(char).startswith("P")
        for char in normalize_transcript_text(text, language)
    )


def unit_digest(units: list[str]) -> str:
    return hashlib.sha256("\0".join(units).encode("utf-8")).hexdigest()


def edit_distance(left: list[str], right: list[str]) -> int:
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row, right_item in enumerate(right, 1):
        current = [row]
        for column, left_item in enumerate(left, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def compare_text(project: str, native: str, language: str) -> dict[str, Any]:
    project_units, native_units = (
        normalize_text(project, language),
        normalize_text(native, language),
    )
    distance = edit_distance(project_units, native_units)
    return {
        "metric": "cer" if language == "zh" else "wer",
        "project_units": len(project_units),
        "native_units": len(native_units),
        "project_sha256": unit_digest(project_units),
        "native_sha256": unit_digest(native_units),
        "project_punctuation": punctuation_count(project, language),
        "native_punctuation": punctuation_count(native, language),
        "edit_distance": distance,
        "difference_rate": None if not native_units else distance / len(native_units),
    }


def compare_reference(hypothesis: str, reference: str, language: str) -> dict[str, Any]:
    hypothesis_units = normalize_text(hypothesis, language)
    reference_units = normalize_text(reference, language)
    if not reference_units:
        raise ValueError("Reference text is empty after normalization")
    distance = edit_distance(hypothesis_units, reference_units)
    return {
        "metric": "cer" if language == "zh" else "wer",
        "hypothesis_units": len(hypothesis_units),
        "reference_units": len(reference_units),
        "hypothesis_sha256": unit_digest(hypothesis_units),
        "reference_sha256": unit_digest(reference_units),
        "hypothesis_punctuation": punctuation_count(hypothesis, language),
        "reference_punctuation": punctuation_count(reference, language),
        "edit_distance": distance,
        "error_rate": distance / len(reference_units),
    }


def build_matrix(
    providers: Iterable[str] = PROVIDERS,
    languages: Iterable[str] = LANGUAGES,
    minutes: Iterable[int] = MINUTES,
    modes: Iterable[str] = MODES,
    repetitions: int = 3,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
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
                        if mode in modes
                    )
    return runs


def run_id(run: dict[str, Any]) -> str:
    return "-".join(
        str(run[key])
        for key in ("provider", "language", "minutes", "mode", "repetition")
    )


def fresh_worker_directory(label: str) -> Path:
    return TMP_DIR / f"{label}-{uuid.uuid4().hex}"


def sample_gpu_mb() -> tuple[float | None, str | None]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        values = [float(line) for line in result.stdout.splitlines() if line.strip()]
        return (sum(values), None) if values else (0.0, None)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def run_worker(run: dict[str, Any], sample: Path, directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "worker.json"
    command = [
        sys.executable,
        "-m",
        "scripts.benchmark",
        "--worker",
        "--audio",
        str(sample.resolve()),
        "--provider",
        run["provider"],
        "--language",
        run["language"],
        "--mode",
        run["mode"],
        "--worker-output",
        str(output.resolve()),
        "--results-dir",
        str((directory / "results").resolve()),
    ]
    started = time.perf_counter()
    process = subprocess.Popen(command, cwd=ROOT)
    peak_rss, peak_gpu, gpu_reason = 0, None, None
    while process.poll() is None:
        try:
            root = psutil.Process(process.pid)
            peak_rss = max(
                peak_rss,
                sum(
                    item.memory_info().rss
                    for item in [root, *root.children(recursive=True)]
                ),
            )
        except (psutil.Error, OSError):
            pass
        gpu, reason = sample_gpu_mb()
        gpu_reason = gpu_reason or reason
        if gpu is not None:
            peak_gpu = max(peak_gpu or 0.0, gpu)
        time.sleep(0.2)
    duration = wav_duration(sample)
    result = (
        read_json(output)
        if output.exists()
        else {"status": "failed", "error": "worker produced no output"}
    )
    return {
        **run,
        **result,
        "run_id": run_id(run),
        "wall_seconds": time.perf_counter() - started,
        "peak_rss_bytes": peak_rss or None,
        "peak_gpu_memory_mb": peak_gpu,
        "gpu_metric_unavailable_reason": gpu_reason if peak_gpu is None else None,
        "audio_seconds": duration,
        "rtf": (time.perf_counter() - started) / duration,
        "returncode": process.returncode,
    }


def transcript_text(manifest_path: Path) -> str:
    manifest = read_json(manifest_path)
    transcript = read_json(manifest_path.parent / manifest["artifacts"]["transcript"])
    return "".join(str(segment["text"]) for segment in transcript["segments"])


def native_whisper_configuration(
    sample_count: int, language: str
) -> tuple[Any, Any, dict[str, Any]]:
    """Give one native Whisper inference the same CPU budget as slicing runs."""
    from scripts.asr.execution import WhisperCpuPolicy
    from scripts.asr.providers import WhisperProvider
    from scripts.runtime_options import TranscribeOptions

    default_options = TranscribeOptions(language=language)
    budget = WhisperCpuPolicy(default_options).execution_identity(sample_count)[
        "cpu_budget"
    ]
    options = TranscribeOptions(language=language, cpu_threads=int(budget))
    policy = WhisperCpuPolicy(options)
    return WhisperProvider(options), policy, policy.execution_identity(sample_count)


def worker(
    audio: Path, provider: str, language: str, mode: str, results_dir: Path
) -> dict[str, Any]:
    from scripts.asr.chunking import ChunkLayout, decode_normalized_audio
    from scripts.asr.execution import Qwen3AsrCudaPolicy
    from scripts.asr.providers import Qwen3AsrProvider
    from scripts.transcribe import run_transcribe

    audio = audio.resolve()
    started = time.perf_counter()
    if mode == "project-slicing":
        outcome = run_transcribe(
            audio, language=language, provider=provider, results_dir=results_dir
        )
        manifest = outcome.manifest_path
        if outcome.pipeline_outcome is None:
            raise RuntimeError(
                "Fresh project-slicing benchmark run produced no pipeline diagnostics"
            )
        metrics = outcome.pipeline_outcome.metrics
        request = read_json(manifest)["request"]
        text = transcript_text(manifest)
        identity = request["execution_policy"]
        provider_identity = request["provider_identity"]
        details = {
            key: getattr(metrics, key)
            for key in (
                "provider_stage_seconds",
                "chunk_count",
                "batch_count",
                "hard_cut_count",
                "max_estimated_speech_duration",
                "speech_load_msre",
            )
        }
    else:
        samples = decode_normalized_audio(audio)
        if provider == "faster-whisper":
            adapter, policy, identity = native_whisper_configuration(
                samples.sample_count, language
            )
        else:
            adapter, policy = Qwen3AsrProvider(language), Qwen3AsrCudaPolicy()
            identity = policy.execution_identity(samples.sample_count)
        provider_identity = adapter.request_identity()
        layout = ChunkLayout(0, 0, samples.sample_count, "source", samples.sample_count)
        provider_started = time.perf_counter()
        prepared = adapter.prepare(identity)
        transcript = adapter.transcribe_one(prepared, samples.samples, layout)
        text = transcript.text
        details = {
            "provider_stage_seconds": time.perf_counter() - provider_started,
            "chunk_count": None,
            "batch_count": None,
            "hard_cut_count": None,
            "provider_native_internal_chunking": "qwen-asr 0.0.6 timestamps: up to 180 seconds"
            if provider == "qwen3-asr"
            else "single provider input with faster-whisper native VAD",
        }
    return {
        "status": "succeeded",
        "worker_seconds": time.perf_counter() - started,
        "text": text,
        "execution_identity": identity,
        "provider_identity": provider_identity,
        **details,
    }


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


_REFERENCE_COMPARISON_FIELDS = {
    "metric",
    "hypothesis_units",
    "reference_units",
    "hypothesis_sha256",
    "reference_sha256",
    "hypothesis_punctuation",
    "reference_punctuation",
    "edit_distance",
    "error_rate",
}


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_reference_comparison(value: Any, language: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _REFERENCE_COMPARISON_FIELDS:
        raise ValueError("Successful benchmark run has an invalid reference comparison")
    if value["metric"] != ("cer" if language == "zh" else "wer"):
        raise ValueError("Reference comparison metric does not match the language")
    integer_fields = (
        "hypothesis_units",
        "reference_units",
        "hypothesis_punctuation",
        "reference_punctuation",
        "edit_distance",
    )
    if any(
        not isinstance(value[field], int)
        or isinstance(value[field], bool)
        or value[field] < 0
        for field in integer_fields
    ):
        raise ValueError("Reference comparison counts must be non-negative integers")
    if value["reference_units"] == 0:
        raise ValueError("Reference comparison has an empty reference")
    if not _valid_digest(value["hypothesis_sha256"]) or not _valid_digest(
        value["reference_sha256"]
    ):
        raise ValueError("Reference comparison contains an invalid digest")
    expected_rate = value["edit_distance"] / value["reference_units"]
    if (
        not isinstance(value["error_rate"], (int, float))
        or isinstance(value["error_rate"], bool)
        or value["error_rate"] != expected_rate
    ):
        raise ValueError("Reference comparison error rate is invalid")
    return value


def _frozen_sample_keys(reference_set: Any) -> set[tuple[str, int]]:
    if not isinstance(reference_set, dict) or set(reference_set) != {
        "schema_version",
        "manifest_sha256",
        "samples",
    }:
        raise ValueError("Benchmark report reference_set is invalid")
    if not _valid_digest(reference_set["manifest_sha256"]):
        raise ValueError("Benchmark report reference manifest digest is invalid")
    samples = reference_set["samples"]
    if not isinstance(samples, list) or not samples:
        raise ValueError("Benchmark report reference_set samples are invalid")
    keys: list[tuple[str, int]] = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {
            "language",
            "minutes",
            "audio_sha256",
            "reference_sha256",
        }:
            raise ValueError("Benchmark report reference sample is invalid")
        if (
            sample["language"] not in LANGUAGES
            or not isinstance(sample["minutes"], int)
            or isinstance(sample["minutes"], bool)
            or sample["minutes"] not in MINUTES
        ):
            raise ValueError("Benchmark report reference sample identity is invalid")
        key = (sample["language"], sample["minutes"])
        if not _valid_digest(sample["audio_sha256"]) or not _valid_digest(
            sample["reference_sha256"]
        ):
            raise ValueError("Benchmark report reference sample digest is invalid")
        keys.append(key)
    if keys != sorted(set(keys)):
        raise ValueError("Benchmark report reference samples must be unique and sorted")
    return set(keys)


def _validate_report(
    report: Any, references: dict[tuple[str, int], dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != {
        "schema_version",
        "comparison_policy",
        "reference_set",
        "environment",
        "warmups",
        "runs",
    }:
        raise ValueError("Benchmark report structure is invalid; start a new report.")
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        raise ValueError("Benchmark report schema is obsolete; start a new report.")
    if report["comparison_policy"] != COMPARISON_POLICY:
        raise ValueError("Benchmark comparison policy changed; start a new report.")
    if not isinstance(report["environment"], dict):
        raise ValueError("Benchmark report environment is invalid")
    if not isinstance(report["warmups"], list) or not isinstance(report["runs"], list):
        raise ValueError("Benchmark report runs are invalid")

    for item in report["warmups"]:
        if not isinstance(item, dict):
            raise ValueError("Benchmark warmup must be an object")
        try:
            provider = item["provider"]
            language = item["language"]
            minute = item["minutes"]
            mode = item["mode"]
            repetition = item["repetition"]
            identifier = item["run_id"]
            status = item["status"]
        except KeyError as exc:
            raise ValueError("Benchmark warmup identity is incomplete") from exc
        if (
            provider not in PROVIDERS
            or (language, minute) not in references
            or mode != "project-slicing"
            or repetition != 0
            or status not in ("succeeded", "failed")
            or identifier != run_id(item)
            or "reference_comparison" in item
        ):
            raise ValueError("Benchmark warmup identity is invalid")

    attempts: dict[str, list[int]] = {}
    successful: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}
    for item in report["runs"]:
        if not isinstance(item, dict):
            raise ValueError("Benchmark run must be an object")
        try:
            provider = item["provider"]
            language = item["language"]
            minute = item["minutes"]
            mode = item["mode"]
            repetition = item["repetition"]
            identifier = item["run_id"]
            attempt = item["attempt"]
            status = item["status"]
            audio_sha256 = item["audio_sha256"]
        except KeyError as exc:
            raise ValueError("Benchmark run identity is incomplete") from exc
        if (
            provider not in PROVIDERS
            or language not in LANGUAGES
            or minute not in MINUTES
            or mode not in MODES
            or not isinstance(repetition, int)
            or isinstance(repetition, bool)
            or repetition < 1
            or not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
            or status not in ("succeeded", "failed")
        ):
            raise ValueError("Benchmark run identity is invalid")
        if identifier != run_id(item):
            raise ValueError("Benchmark run_id does not match its identity")
        reference = references.get((language, minute))
        if reference is None or audio_sha256 != reference["audio_sha256"]:
            raise ValueError(
                "Benchmark run audio identity does not match reference_set"
            )
        attempts.setdefault(identifier, []).append(attempt)
        if status == "succeeded":
            if not isinstance(item.get("text"), str):
                raise ValueError("Successful benchmark run is missing text")
            actual = _validate_reference_comparison(
                item.get("reference_comparison"), language
            )
            expected = compare_reference(item["text"], reference["text"], language)
            if actual != expected:
                raise ValueError(
                    "Benchmark run reference comparison does not match its text"
                )
            key = (provider, language, mode, minute, repetition)
            if key in successful:
                raise ValueError("Benchmark report contains duplicate successful runs")
            successful[key] = item
        elif "reference_comparison" in item:
            raise ValueError(
                "Failed benchmark run must not have a reference comparison"
            )
        if mode == "provider-native" and "output_comparison" in item:
            raise ValueError("provider-native run must not have output_comparison")
    if any(values != list(range(1, len(values) + 1)) for values in attempts.values()):
        raise ValueError("Benchmark run attempt sequence is invalid")

    for key, project in successful.items():
        provider, language, mode, minute, repetition = key
        if mode != "project-slicing":
            continue
        native = successful.get(
            (provider, language, "provider-native", minute, repetition)
        )
        if native is None:
            if "output_comparison" in project:
                raise ValueError("Unpaired run must not have output_comparison")
            continue
        expected = compare_text(project["text"], native["text"], language)
        if project.get("output_comparison") != expected:
            raise ValueError("Paired run output_comparison is invalid")
    return report


def summarize(report: dict[str, Any]) -> str:
    reference_set_value = report.get("reference_set")
    _frozen_sample_keys(reference_set_value)
    if not isinstance(reference_set_value, dict):
        raise ValueError("Benchmark report reference_set is invalid")
    reference_set = reference_set_value
    reference_identities = {
        (item["language"], item["minutes"]): item for item in reference_set["samples"]
    }
    successful = [item for item in report["runs"] if item.get("status") == "succeeded"]
    for item in successful:
        comparison = _validate_reference_comparison(
            item.get("reference_comparison"), item["language"]
        )
        identity = reference_identities.get((item["language"], item["minutes"]))
        if (
            identity is None
            or comparison["reference_sha256"] != identity["reference_sha256"]
        ):
            raise ValueError(
                "Successful benchmark run reference comparison has the wrong identity"
            )
    lines = [
        "# Audio transcription benchmark",
        "",
        f"Reference manifest SHA256: `{reference_set['manifest_sha256']}`",
        "",
        "Only successful runs are summarized.",
        "",
    ]
    for provider in PROVIDERS:
        rows = [item for item in successful if item["provider"] == provider]
        if not rows:
            continue
        lines += [
            f"## {provider}",
            "",
            "| Language | Minutes | Mode | Median wall | Median RTF | Provider stage | Relative speed | Reference CER/WER | Mode difference | Punctuation (hypothesis/reference) |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for language in LANGUAGES:
            for minute in MINUTES:
                pair = [
                    item
                    for item in rows
                    if item["language"] == language and item["minutes"] == minute
                ]
                medians = {
                    mode: statistics.median(
                        item["wall_seconds"] for item in pair if item["mode"] == mode
                    )
                    for mode in MODES
                    if any(item["mode"] == mode for item in pair)
                }
                mode_comparisons = [
                    item["output_comparison"]
                    for item in pair
                    if item["mode"] == "project-slicing" and "output_comparison" in item
                ]
                for mode in MODES:
                    selected = [item for item in pair if item["mode"] == mode]
                    if not selected:
                        continue
                    wall = medians[mode]
                    speed = (
                        medians.get("provider-native", 0) / wall
                        if mode == "project-slicing" and wall
                        else None
                    )
                    difference = (
                        statistics.median(
                            item["difference_rate"]
                            for item in mode_comparisons
                            if item["difference_rate"] is not None
                        )
                        if any(
                            item["difference_rate"] is not None
                            for item in mode_comparisons
                        )
                        else None
                    )
                    reference_comparisons = [
                        item["reference_comparison"] for item in selected
                    ]
                    reference_rate = statistics.median(
                        item["error_rate"] for item in reference_comparisons
                    )
                    punctuation = (
                        f"{statistics.median(item['hypothesis_punctuation'] for item in reference_comparisons):g}/"
                        f"{statistics.median(item['reference_punctuation'] for item in reference_comparisons):g}"
                    )
                    lines.append(
                        f"| {language} | {minute} | {mode} | {wall:.3f}s | {statistics.median(item['rtf'] for item in selected):.4f} | {statistics.median(item['provider_stage_seconds'] for item in selected):.3f}s | {f'{speed:.3f}x' if speed is not None else '—'} | {reference_rate:.3%} | {f'{difference:.3%}' if difference is not None and mode == 'project-slicing' else '—'} | {punctuation} |"
                    )
        lines.append("")
    return "\n".join(lines) + "\n"


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    report_path = args.report
    matrix = build_matrix(
        args.provider or PROVIDERS,
        args.language or LANGUAGES,
        args.minutes or MINUTES,
        args.mode or MODES,
        args.repetitions,
    )
    selected_keys = {(item["language"], item["minutes"]) for item in matrix}
    reference_manifest = load_reference_manifest(REFERENCE_MANIFEST)

    if report_path.exists():
        report = read_json(report_path)
        if (
            not isinstance(report, dict)
            or report.get("schema_version") != REPORT_SCHEMA_VERSION
        ):
            raise ValueError("Benchmark report schema is obsolete; start a new report.")
        frozen_keys = _frozen_sample_keys(report.get("reference_set"))
        if not selected_keys.issubset(frozen_keys):
            raise ValueError(
                "Selected samples extend the frozen reference_set; use a new report path."
            )
        references = load_reference_samples(
            reference_manifest,
            frozen_keys,
            data_dir=DATA_DIR,
            samples_manifest_path=DATA_DIR / "samples.json",
            normalize_units=normalize_text,
            unit_digest=unit_digest,
        )
        if report["reference_set"] != freeze_reference_set(
            reference_manifest, references
        ):
            raise ValueError(
                "Benchmark reference identity changed; start a new report."
            )
        report = _validate_report(report, references)
    else:
        references = load_reference_samples(
            reference_manifest,
            selected_keys,
            data_dir=DATA_DIR,
            samples_manifest_path=DATA_DIR / "samples.json",
            normalize_units=normalize_text,
            unit_digest=unit_digest,
        )
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "comparison_policy": COMPARISON_POLICY,
            "reference_set": freeze_reference_set(reference_manifest, references),
            "environment": environment(),
            "warmups": [],
            "runs": [],
        }

    succeeded = {
        item["run_id"] for item in report["runs"] if item.get("status") == "succeeded"
    }
    warmed = {
        item["provider"]
        for item in report["warmups"]
        if item.get("status") == "succeeded"
    }
    for provider in dict.fromkeys(item["provider"] for item in matrix):
        provider_runs = [item for item in matrix if item["provider"] == provider]
        if provider not in warmed:
            first = provider_runs[0]
            warm = {
                "provider": provider,
                "language": first["language"],
                "minutes": first["minutes"],
                "mode": "project-slicing",
                "repetition": 0,
            }
            report["warmups"].append(
                run_worker(
                    warm,
                    DATA_DIR / f"{warm['language']}-{warm['minutes']}min.wav",
                    fresh_worker_directory(
                        f"warmup-{provider}-{len(report['warmups']) + 1}"
                    ),
                )
            )
            write_json_atomic(report_path, report)
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
            write_json_atomic(report_path, report)
            report_path.with_suffix(".md").write_text(
                summarize(report), encoding="utf-8", newline="\n"
            )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare project slicing with provider-native transcription."
    )
    parser.add_argument("--provider", action="append", choices=PROVIDERS)
    parser.add_argument("--language", action="append", choices=LANGUAGES)
    parser.add_argument("--minutes", action="append", type=int, choices=MINUTES)
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "benchmark" / "reports" / f"{date.today().isoformat()}.json",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--audio", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--results-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.worker:
        if (
            not args.audio
            or not args.worker_output
            or not args.results_dir
            or not args.provider
            or not args.language
            or not args.mode
        ):
            raise SystemExit("worker arguments are incomplete")
        try:
            result = worker(
                args.audio,
                args.provider[0],
                args.language[0],
                args.mode[0],
                args.results_dir,
            )
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        write_json_atomic(args.worker_output, result)
        return 0 if result["status"] == "succeeded" else 1
    run_benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
