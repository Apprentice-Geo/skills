from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

from benchmark import LANGUAGES, MINUTES, MODES, PROVIDERS
from benchmark.metrics import COMPARISON_POLICY
from scripts.io_utils import write_json_atomic

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
_OUTPUT_COMPARISON_FIELDS = {
    "metric",
    "project_units",
    "native_units",
    "project_sha256",
    "native_sha256",
    "project_punctuation",
    "native_punctuation",
    "edit_distance",
    "difference_rate",
}


def run_id(run: dict[str, Any]) -> str:
    return "-".join(
        str(run[key])
        for key in ("provider", "language", "minutes", "mode", "repetition")
    )


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_session_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _validate_environment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"hardware", "audit"}:
        raise ValueError(
            "Benchmark report environment is invalid; use a new report path."
        )
    hardware = value["hardware"]
    if not isinstance(hardware, dict) or set(hardware) != {
        "cpu_model",
        "logical_cpu_count",
        "physical_memory_bytes",
        "gpus",
    }:
        raise ValueError(
            "Benchmark report hardware identity is invalid; use a new report path."
        )
    if (
        not isinstance(hardware["cpu_model"], str)
        or not hardware["cpu_model"]
        or not isinstance(hardware["logical_cpu_count"], int)
        or isinstance(hardware["logical_cpu_count"], bool)
        or hardware["logical_cpu_count"] < 1
        or not isinstance(hardware["physical_memory_bytes"], int)
        or isinstance(hardware["physical_memory_bytes"], bool)
        or hardware["physical_memory_bytes"] < 1
        or not isinstance(hardware["gpus"], list)
    ):
        raise ValueError("Benchmark report hardware identity is invalid")
    indices = []
    for gpu in hardware["gpus"]:
        if (
            not isinstance(gpu, dict)
            or set(gpu) != {"index", "name", "memory_total_bytes"}
            or not isinstance(gpu["index"], int)
            or isinstance(gpu["index"], bool)
            or gpu["index"] < 0
            or not isinstance(gpu["name"], str)
            or not gpu["name"]
            or not isinstance(gpu["memory_total_bytes"], int)
            or isinstance(gpu["memory_total_bytes"], bool)
            or gpu["memory_total_bytes"] < 1
        ):
            raise ValueError("Benchmark report GPU identity is invalid")
        indices.append(gpu["index"])
    if indices != sorted(set(indices)):
        raise ValueError("Benchmark report GPUs must be unique and sorted")
    audit = value["audit"]
    if (
        not isinstance(audit, dict)
        or set(audit)
        != {"platform", "python", "commit", "dependencies", "model_revisions"}
        or not isinstance(audit["platform"], str)
        or not isinstance(audit["python"], str)
        or audit["commit"] is not None
        and not isinstance(audit["commit"], str)
        or audit["dependencies"] is not None
        and not isinstance(audit["dependencies"], str)
        or not isinstance(audit["model_revisions"], dict)
    ):
        raise ValueError("Benchmark report audit information is invalid")
    return value


def _validate_model_configuration(provider: str, value: Any) -> dict[str, Any]:
    fields = (
        {"model", "device", "compute_type", "cpu_threads", "num_workers"}
        if provider == "faster-whisper"
        else {"model", "aligner", "device", "dtype", "batch_size", "max_new_tokens"}
    )
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Benchmark model configuration is invalid")
    identities = [value["model"]]
    if provider == "qwen3-asr":
        identities.append(value["aligner"])
    if any(
        not isinstance(identity, dict)
        or set(identity) != {"repo", "revision", "logical_id"}
        or any(
            not isinstance(identity[field], str) or not identity[field]
            for field in identity
        )
        for identity in identities
    ):
        raise ValueError("Benchmark model identity is invalid")
    string_fields = (
        ("device", "compute_type")
        if provider == "faster-whisper"
        else ("device", "dtype")
    )
    integer_fields = (
        ("cpu_threads", "num_workers")
        if provider == "faster-whisper"
        else ("batch_size", "max_new_tokens")
    )
    if any(
        not isinstance(value[field], str) or not value[field] for field in string_fields
    ) or any(
        not isinstance(value[field], int)
        or isinstance(value[field], bool)
        or value[field] < 1
        for field in integer_fields
    ):
        raise ValueError("Benchmark model loading parameters are invalid")
    return value


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "providers",
        "languages",
        "minutes",
        "modes",
        "repetitions",
    }:
        raise ValueError("Benchmark report config is invalid; use a new report path.")
    for field, allowed in (
        ("providers", PROVIDERS),
        ("languages", LANGUAGES),
        ("minutes", MINUTES),
        ("modes", MODES),
    ):
        selected = value[field]
        if (
            not isinstance(selected, list)
            or not selected
            or selected != [item for item in allowed if item in selected]
            or len(selected) != len(set(selected))
        ):
            raise ValueError(
                f"Benchmark report config.{field} is not canonical; use a new report path."
            )
    repetitions = value["repetitions"]
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or repetitions < 1
    ):
        raise ValueError(
            "Benchmark report config.repetitions is invalid; use a new report path."
        )
    return value


def frozen_sample_keys(reference_set: Any) -> set[tuple[str, int]]:
    if not isinstance(reference_set, dict) or set(reference_set) != {
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
        key = (sample["language"], sample["minutes"])
        if (
            sample["language"] not in LANGUAGES
            or not isinstance(sample["minutes"], int)
            or isinstance(sample["minutes"], bool)
            or sample["minutes"] not in MINUTES
            or not _valid_digest(sample["audio_sha256"])
            or not _valid_digest(sample["reference_sha256"])
        ):
            raise ValueError("Benchmark report reference sample identity is invalid")
        keys.append(key)
    if keys != sorted(set(keys)):
        raise ValueError("Benchmark report reference samples must be unique and sorted")
    return set(keys)


def _validate_comparison(
    value: Any, language: str, *, reference: bool
) -> dict[str, Any]:
    fields = _REFERENCE_COMPARISON_FIELDS if reference else _OUTPUT_COMPARISON_FIELDS
    if not isinstance(value, dict) or set(value) != fields:
        name = "reference comparison" if reference else "output comparison"
        raise ValueError(f"Successful benchmark run has an invalid {name}")
    if value["metric"] != ("cer" if language == "zh" else "wer"):
        raise ValueError("Benchmark comparison metric does not match the language")
    prefix = "reference" if reference else "native"
    count_fields = (
        (
            "hypothesis_units",
            "reference_units",
            "hypothesis_punctuation",
            "reference_punctuation",
            "edit_distance",
        )
        if reference
        else (
            "project_units",
            "native_units",
            "project_punctuation",
            "native_punctuation",
            "edit_distance",
        )
    )
    if any(
        not isinstance(value[field], int)
        or isinstance(value[field], bool)
        or value[field] < 0
        for field in count_fields
    ):
        raise ValueError("Benchmark comparison counts must be non-negative integers")
    denominator = value[f"{prefix}_units"]
    rate_field = "error_rate" if reference else "difference_rate"
    rate = value[rate_field]
    expected = (
        None
        if not denominator and not reference
        else value["edit_distance"] / denominator
    )
    if reference and not denominator:
        raise ValueError("Reference comparison has an empty reference")
    if rate != expected or (rate is not None and isinstance(rate, bool)):
        raise ValueError(
            f"Benchmark comparison {rate_field.replace('_', ' ')} is invalid"
        )
    digest_fields = (
        ("hypothesis_sha256", "reference_sha256")
        if reference
        else ("project_sha256", "native_sha256")
    )
    if any(not _valid_digest(value[field]) for field in digest_fields):
        raise ValueError("Benchmark comparison contains an invalid digest")
    return value


def validate_report(
    report: Any, references: dict[tuple[str, int], dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != {
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
    config = validate_config(report["config"])
    policy = report["comparison_policy"]
    if (
        not isinstance(policy, dict)
        or set(policy) != set(COMPARISON_POLICY)
        or not isinstance(policy["text_normalization"], dict)
        or any(
            not isinstance(policy[field], str)
            for field in set(COMPARISON_POLICY) - {"text_normalization"}
        )
    ):
        raise ValueError("Benchmark report comparison policy is invalid")
    _validate_environment(report["environment"])
    if not isinstance(report["warmups"], list) or not isinstance(report["runs"], list):
        raise ValueError("Benchmark report runs are invalid")
    frozen = frozen_sample_keys(report["reference_set"])
    expected_samples = {
        (language, minute)
        for language in config["languages"]
        for minute in config["minutes"]
    }
    if frozen != expected_samples or set(references) != expected_samples:
        raise ValueError("Benchmark report reference_set does not match config")

    successful_warmups: list[dict[str, Any]] = []
    for item in report["warmups"]:
        if not isinstance(item, dict):
            raise ValueError("Benchmark warmup must be an object")
        try:
            identity = (
                item["provider"],
                item["language"],
                item["minutes"],
                item["mode"],
                item["repetition"],
                item["run_id"],
                item["status"],
            )
        except KeyError as exc:
            raise ValueError("Benchmark warmup identity is incomplete") from exc
        provider, language, minute, mode, repetition, identifier, status = identity
        if identifier != run_id(item):
            raise ValueError("Benchmark run_id does not match its identity")
        if any(
            field in item
            for field in (
                "peak_rss_bytes",
                "peak_gpu_memory_mb",
                "gpu_metric_unavailable_reason",
            )
        ):
            raise ValueError("Benchmark warmup contains removed resource metrics")
        if (
            provider not in config["providers"]
            or language not in config["languages"]
            or minute not in config["minutes"]
            or mode not in config["modes"]
            or repetition != 0
            or status not in ("succeeded", "failed")
            or not _valid_session_id(item.get("session_id"))
            or "reference_comparison" in item
            or "output_comparison" in item
        ):
            raise ValueError("Benchmark warmup identity is invalid")
        if status == "succeeded":
            if not isinstance(item.get("execution_identity"), dict) or not isinstance(
                item.get("provider_identity"), dict
            ):
                raise ValueError(
                    "Successful benchmark warmup is missing audit identity"
                )
            configuration = _validate_model_configuration(
                provider, item.get("model_configuration")
            )
            if any(
                warmup["provider"] == provider
                and warmup["session_id"] == item["session_id"]
                and warmup["model_configuration"] == configuration
                for warmup in successful_warmups
            ):
                raise ValueError(
                    "Benchmark warmup duplicates a prepared session configuration"
                )
            successful_warmups.append(item)

    attempts: dict[str, list[int]] = {}
    successful: dict[tuple[str, str, int, str, int], dict[str, Any]] = {}
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
        if identifier != run_id(item):
            raise ValueError("Benchmark run_id does not match its identity")
        if any(
            field in item
            for field in (
                "peak_rss_bytes",
                "peak_gpu_memory_mb",
                "gpu_metric_unavailable_reason",
            )
        ):
            raise ValueError("Benchmark run contains removed resource metrics")
        if (
            provider not in config["providers"]
            or language not in config["languages"]
            or minute not in config["minutes"]
            or mode not in config["modes"]
            or not isinstance(repetition, int)
            or isinstance(repetition, bool)
            or repetition not in range(1, config["repetitions"] + 1)
            or not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
            or status not in ("succeeded", "failed")
            or not _valid_session_id(item.get("session_id"))
        ):
            raise ValueError("Benchmark run identity is outside the frozen config")
        reference_item = references[(language, minute)]
        if audio_sha256 != reference_item["audio_sha256"]:
            raise ValueError(
                "Benchmark run audio identity does not match reference_set"
            )
        attempts.setdefault(identifier, []).append(attempt)
        if status == "succeeded":
            if not isinstance(item.get("text"), str):
                raise ValueError("Successful benchmark run is missing text")
            if not isinstance(item.get("execution_identity"), dict) or not isinstance(
                item.get("provider_identity"), dict
            ):
                raise ValueError("Successful benchmark run is missing audit identity")
            configuration = _validate_model_configuration(
                provider, item.get("model_configuration")
            )
            if not any(
                warmup["provider"] == provider
                and warmup["session_id"] == item["session_id"]
                and warmup["model_configuration"] == configuration
                for warmup in successful_warmups
            ):
                raise ValueError(
                    "Successful benchmark run has no matching session warmup"
                )
            comparison = _validate_comparison(
                item.get("reference_comparison"), language, reference=True
            )
            if comparison["reference_sha256"] != reference_item["reference_sha256"]:
                raise ValueError(
                    "Benchmark run reference comparison has the wrong identity"
                )
            key = (provider, language, minute, mode, repetition)
            if key in successful:
                raise ValueError("Benchmark report contains duplicate successful runs")
            successful[key] = item
        elif "reference_comparison" in item or "output_comparison" in item:
            raise ValueError("Failed benchmark run must not have a comparison")
        if mode == "provider-native" and "output_comparison" in item:
            raise ValueError("provider-native run must not have output_comparison")
    if any(values != list(range(1, len(values) + 1)) for values in attempts.values()):
        raise ValueError("Benchmark run attempt sequence is invalid")
    if any(
        item["status"] == "succeeded"
        and item["attempt"] != attempts[item["run_id"]][-1]
        for item in report["runs"]
    ):
        raise ValueError("Benchmark run contains attempts after success")

    for (provider, language, minute, mode, repetition), project in successful.items():
        if mode != "project-slicing":
            continue
        native = successful.get(
            (provider, language, minute, "provider-native", repetition)
        )
        if native is None:
            if "output_comparison" in project:
                raise ValueError("Unpaired run must not have output_comparison")
        else:
            output = _validate_comparison(
                project.get("output_comparison"), language, reference=False
            )
            project_reference = project["reference_comparison"]
            native_reference = native["reference_comparison"]
            if (
                output["project_units"] != project_reference["hypothesis_units"]
                or output["project_sha256"] != project_reference["hypothesis_sha256"]
                or output["project_punctuation"]
                != project_reference["hypothesis_punctuation"]
                or output["native_units"] != native_reference["hypothesis_units"]
                or output["native_sha256"] != native_reference["hypothesis_sha256"]
                or output["native_punctuation"]
                != native_reference["hypothesis_punctuation"]
            ):
                raise ValueError(
                    "Benchmark output comparison does not match its paired runs"
                )
    return report


def summarize(report: dict[str, Any]) -> str:
    reference_set = report.get("reference_set")
    frozen_sample_keys(reference_set)
    if not isinstance(reference_set, dict):
        raise ValueError("Benchmark report reference_set is invalid")
    identities = {
        (item["language"], item["minutes"]): item for item in reference_set["samples"]
    }
    successful = [item for item in report["runs"] if item.get("status") == "succeeded"]
    for item in successful:
        comparison = _validate_comparison(
            item.get("reference_comparison"), item["language"], reference=True
        )
        identity = identities.get((item["language"], item["minutes"]))
        if (
            identity is None
            or comparison["reference_sha256"] != identity["reference_sha256"]
        ):
            raise ValueError(
                "Successful benchmark run reference comparison has the wrong identity"
            )
    hardware = _validate_environment(report.get("environment"))["hardware"]
    memory_gib = hardware["physical_memory_bytes"] / 1024**3
    gpu_lines = [
        f"- GPU {gpu['index']}: {gpu['name']} "
        f"({gpu['memory_total_bytes'] / 1024**3:.2f} GiB total)"
        for gpu in hardware["gpus"]
    ] or ["- GPU: none detected"]
    lines = [
        "# Audio transcription benchmark",
        "",
        f"Reference manifest SHA256: `{reference_set['manifest_sha256']}`",
        "",
        "Only successful runs are summarized.",
        "",
        "## Method",
        "",
        "This benchmark compares the `project-slicing` and `provider-native` end-to-end strategies. Wall time, RTF, relative speed, and text differences cannot be attributed to the chunk optimizer alone.",
        "",
        "Each provider uses a persistent worker process. Every model configuration is warmed with the same sample and mode immediately before its first measured run in that worker session, and the prepared model is then reused.",
        "",
        "Memory and GPU-memory usage are not measured or compared. Device memory totals below describe hardware capacity only.",
        "",
        "## Test device",
        "",
        f"- CPU: {hardware['cpu_model']} ({hardware['logical_cpu_count']} logical cores)",
        f"- Physical memory: {memory_gib:.2f} GiB total",
        *gpu_lines,
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
                    rates = [
                        item["difference_rate"]
                        for item in mode_comparisons
                        if item["difference_rate"] is not None
                    ]
                    difference = statistics.median(rates) if rates else None
                    comparisons = [item["reference_comparison"] for item in selected]
                    reference_rate = statistics.median(
                        item["error_rate"] for item in comparisons
                    )
                    punctuation = (
                        f"{statistics.median(item['hypothesis_punctuation'] for item in comparisons):g}/"
                        f"{statistics.median(item['reference_punctuation'] for item in comparisons):g}"
                    )
                    lines.append(
                        f"| {language} | {minute} | {mode} | {wall:.3f}s | {statistics.median(item['rtf'] for item in selected):.4f} | {statistics.median(item['provider_stage_seconds'] for item in selected):.3f}s | {f'{speed:.3f}x' if speed is not None else '—'} | {reference_rate:.3%} | {f'{difference:.3%}' if difference is not None and mode == 'project-slicing' else '—'} | {punctuation} |"
                    )
        lines.append("")
    return "\n".join(lines) + "\n"


def write_report(path: Path, report: dict[str, Any]) -> None:
    write_json_atomic(path, report)
    path.with_suffix(".md").write_text(
        summarize(report), encoding="utf-8", newline="\n"
    )
