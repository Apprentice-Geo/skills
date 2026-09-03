from __future__ import annotations

import argparse
import hashlib
import json
import os
import wave
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from benchmark.reference import (
    freeze_reference_set,
    load_reference_manifest,
    load_reference_samples,
)
from scripts import benchmark
from scripts.io_utils import sha256_file, write_json_atomic


def _write_wav(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 160)
    return sha256_file(path)


def _reference_tree(
    root: Path, *, actual_samples: set[tuple[str, int]] | None = None
) -> tuple[Path, Path, Path, dict[str, Any]]:
    reference_root = root / "references"
    data = root / "data"
    reference_root.mkdir(parents=True)
    data.mkdir(parents=True)
    all_samples = {
        (language, minute)
        for language in benchmark.LANGUAGES
        for minute in benchmark.MINUTES
    }
    actual_samples = all_samples if actual_samples is None else actual_samples
    digests: dict[tuple[str, int], str] = {}
    template = data / "template.wav"
    template_digest = _write_wav(template)
    for key in all_samples:
        language, minute = key
        digests[key] = template_digest
        if key in actual_samples:
            (data / f"{language}-{minute}min.wav").write_bytes(template.read_bytes())
    template.unlink()
    samples_json = {
        "sample_rate": 16_000,
        "channels": 1,
        "lookahead_seconds": 300,
        "sources": [
            {
                "language": language,
                "cuts": [
                    {"minutes": minute, "sha256": digests[(language, minute)]}
                    for minute in benchmark.MINUTES
                ],
            }
            for language in benchmark.LANGUAGES
        ],
    }
    write_json_atomic(data / "samples.json", samples_json)

    languages: dict[str, Any] = {}
    previous = 0
    for language in benchmark.LANGUAGES:
        parts = []
        previous = 0
        for minute in benchmark.MINUTES:
            relative = f"{language}/{previous:03d}-{minute:03d}.txt"
            part = reference_root / relative
            part.parent.mkdir(exist_ok=True)
            text = f"{language} reference through {minute}.\n"
            part.write_text(text, encoding="utf-8", newline="\n")
            parts.append(
                {
                    "through_minutes": minute,
                    "path": relative,
                    "sha256": sha256_file(part),
                    "sample_audio_sha256": digests[(language, minute)],
                }
            )
            previous = minute
        languages[language] = {
            "source": {
                "work": f"{language} work",
                "author": "author",
                "reader": "reader",
                "audio_url": "https://example.test/audio",
                "text_url": "https://example.test/text",
            },
            "parts": parts,
        }
    manifest = {
        "schema_version": 1,
        "languages": languages,
    }
    manifest_path = reference_root / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    return manifest_path, data, data / "samples.json", manifest


def _load(
    manifest_path: Path,
    data: Path,
    samples_path: Path,
    selection: set[tuple[str, int]],
) -> tuple[dict[str, Any], dict[tuple[str, int], dict[str, Any]]]:
    manifest = load_reference_manifest(manifest_path)
    samples = load_reference_samples(
        manifest,
        selection,
        data_dir=data,
        samples_manifest_path=samples_path,
        normalize_units=benchmark.normalize_text,
        unit_digest=benchmark.unit_digest,
    )
    return manifest, samples


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"unknown": True}), "unknown fields"),
        (
            lambda value: value["languages"]["zh"]["source"].pop("author"),
            "missing fields",
        ),
        (
            lambda value: value["languages"]["en"]["parts"][0].update(
                {"unknown": True}
            ),
            "unknown fields",
        ),
        (lambda value: value.update({"schema_version": "1"}), "schema"),
        (
            lambda value: value["languages"].update({"fr": value["languages"]["en"]}),
            "unknown fields",
        ),
        (
            lambda value: value["languages"]["zh"]["parts"][0].update(
                {"sha256": "A" * 64}
            ),
            "lowercase SHA256",
        ),
        (
            lambda value: value["languages"]["zh"]["parts"][0].update(
                {"through_minutes": 7}
            ),
            "through_minutes",
        ),
        (
            lambda value: value["languages"]["en"]["parts"][0].update(
                {"path": value["languages"]["zh"]["parts"][0]["path"]}
            ),
            "duplicated",
        ),
        (
            lambda value: value["languages"]["zh"]["parts"][0].update(
                {"path": "../escape.txt"}
            ),
            "safe relative",
        ),
    ],
)
def test_reference_manifest_rejects_invalid_metadata(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    manifest_path, _data, _samples, value = _reference_tree(tmp_path)
    changed = deepcopy(value)
    mutation(changed)
    write_json_atomic(manifest_path, changed)

    with pytest.raises(ValueError, match=message):
        load_reference_manifest(manifest_path)


@pytest.mark.parametrize(
    ("raw", "message", "update_digest"),
    [
        (b"\xef\xbb\xbftext", "BOM", True),
        (b"line\r\n", "LF line endings", True),
        (b"\xff", "valid UTF-8", True),
        (b"a\0b", "NUL", True),
        (b"changed\n", "SHA256 mismatch", False),
        (b"...\n", "empty after normalization", True),
    ],
)
def test_reference_parts_reject_invalid_content(
    tmp_path: Path, raw: bytes, message: str, update_digest: bool
) -> None:
    manifest_path, data, samples_path, value = _reference_tree(tmp_path)
    relative = value["languages"]["en"]["parts"][0]["path"]
    part = manifest_path.parent / relative
    part.write_bytes(raw)
    if update_digest:
        value["languages"]["en"]["parts"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        write_json_atomic(manifest_path, value)

    manifest = load_reference_manifest(manifest_path)
    with pytest.raises(ValueError, match=message):
        load_reference_samples(
            manifest,
            {("en", 8)},
            data_dir=data,
            samples_manifest_path=samples_path,
            normalize_units=benchmark.normalize_text,
            unit_digest=benchmark.unit_digest,
        )


def test_reference_loader_rejects_missing_selected_part(tmp_path: Path) -> None:
    manifest_path, data, samples_path, value = _reference_tree(tmp_path)
    relative = value["languages"]["zh"]["parts"][0]["path"]
    (manifest_path.parent / relative).unlink()

    with pytest.raises(ValueError, match="regular file"):
        _load(manifest_path, data, samples_path, {("zh", 8)})


def test_reference_loader_rejects_audio_identity_mismatch(tmp_path: Path) -> None:
    manifest_path, data, samples_path, value = _reference_tree(tmp_path)
    value["languages"]["zh"]["parts"][0]["sample_audio_sha256"] = "f" * 64
    write_json_atomic(manifest_path, value)

    with pytest.raises(ValueError, match="samples.json"):
        _load(manifest_path, data, samples_path, {("zh", 8)})

    _write_wav(data / "zh-8min.wav")
    (data / "zh-8min.wav").write_bytes(b"different")
    value["languages"]["zh"]["parts"][0]["sample_audio_sha256"] = json.loads(
        samples_path.read_text(encoding="utf-8")
    )["sources"][0]["cuts"][0]["sha256"]
    write_json_atomic(manifest_path, value)
    with pytest.raises(ValueError, match="sample SHA256 mismatch"):
        _load(manifest_path, data, samples_path, {("zh", 8)})


def test_reference_loader_rejects_invalid_wav_before_worker(tmp_path: Path) -> None:
    manifest_path, data, samples_path, value = _reference_tree(tmp_path)
    audio = data / "zh-8min.wav"
    audio.write_bytes(b"not a wave file")
    digest = sha256_file(audio)
    value["languages"]["zh"]["parts"][0]["sample_audio_sha256"] = digest
    write_json_atomic(manifest_path, value)
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    samples["sources"][0]["cuts"][0]["sha256"] = digest
    write_json_atomic(samples_path, samples)

    with pytest.raises(ValueError, match="readable PCM WAV"):
        _load(manifest_path, data, samples_path, {("zh", 8)})


def test_reference_loader_rejects_symbolic_link(tmp_path: Path) -> None:
    manifest_path, data, samples_path, value = _reference_tree(tmp_path)
    relative = value["languages"]["zh"]["parts"][0]["path"]
    part = manifest_path.parent / relative
    target = part.with_suffix(".target")
    part.replace(target)
    try:
        os.symlink(target, part)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    manifest = load_reference_manifest(manifest_path)
    with pytest.raises(ValueError, match="symbolic link"):
        load_reference_samples(
            manifest,
            {("zh", 8)},
            data_dir=data,
            samples_manifest_path=samples_path,
            normalize_units=benchmark.normalize_text,
            unit_digest=benchmark.unit_digest,
        )


def test_fixed_references_match_all_samples_and_are_cumulative_prefixes() -> None:
    required_audio = [
        benchmark.DATA_DIR / f"{language}-{minute}min.wav"
        for language in benchmark.LANGUAGES
        for minute in benchmark.MINUTES
    ]
    if not (benchmark.DATA_DIR / "samples.json").is_file() or not all(
        path.is_file() for path in required_audio
    ):
        pytest.skip("ignored local benchmark audio is not prepared")
    manifest = load_reference_manifest(benchmark.REFERENCE_MANIFEST)
    selection = {
        (language, minute)
        for language in benchmark.LANGUAGES
        for minute in benchmark.MINUTES
    }
    samples = load_reference_samples(
        manifest,
        selection,
        data_dir=benchmark.DATA_DIR,
        samples_manifest_path=benchmark.DATA_DIR / "samples.json",
        normalize_units=benchmark.normalize_text,
        unit_digest=benchmark.unit_digest,
    )

    assert len(samples) == 8
    for language in benchmark.LANGUAGES:
        prior: list[str] = []
        for minute in benchmark.MINUTES:
            units = samples[(language, minute)]["units"]
            assert units
            assert units[: len(prior)] == prior
            prior = units


def _args(
    report: Path, *, language: str = "en", minutes: int = 16
) -> argparse.Namespace:
    return argparse.Namespace(
        report=report,
        provider=["faster-whisper"],
        language=[language],
        minutes=[minutes],
        mode=["project-slicing"],
        repetitions=1,
    )


def _fake_worker(calls: list[dict[str, Any]], fail_first: bool = False):
    def run(run: dict[str, Any], sample: Path, _directory: Path) -> dict[str, Any]:
        calls.append({**run, "sample": sample})
        failed = (
            fail_first
            and run["repetition"] == 1
            and len([item for item in calls if item["repetition"] == 1]) == 1
        )
        return {
            **run,
            "status": "failed" if failed else "succeeded",
            **({"error": "expected"} if failed else {"text": "reference"}),
            "run_id": benchmark.run_id(run),
            "wall_seconds": 1.0,
            "rtf": 0.1,
            "provider_stage_seconds": 0.5,
        }

    return run


def test_orchestration_freezes_samples_warms_selected_and_allows_subset_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path, data, _samples_path, _value = _reference_tree(
        tmp_path, actual_samples={("en", 8), ("en", 16)}
    )
    (manifest_path.parent / "en" / "016-032.txt").unlink()
    (manifest_path.parent / "zh" / "000-008.txt").unlink()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(benchmark, "REFERENCE_MANIFEST", manifest_path)
    monkeypatch.setattr(benchmark, "DATA_DIR", data)
    monkeypatch.setattr(benchmark, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(benchmark, "environment", lambda: {"test": True})
    monkeypatch.setattr(benchmark, "run_worker", _fake_worker(calls))
    report_path = tmp_path / "report.json"
    initial_args = _args(report_path, minutes=8)
    initial_args.minutes = [8, 16]

    report = benchmark.run_benchmark(initial_args)

    assert [
        (item["language"], item["minutes"])
        for item in report["reference_set"]["samples"]
    ] == [("en", 8), ("en", 16)]
    assert calls[0]["repetition"] == 0
    assert calls[0]["sample"] == data / "en-8min.wav"
    assert "reference_comparison" not in report["warmups"][0]
    assert report["runs"][0]["reference_comparison"]["metric"] == "wer"

    calls.clear()
    resumed = benchmark.run_benchmark(_args(report_path, minutes=8))
    assert resumed["runs"] == report["runs"]
    assert calls == []

    extension = _args(report_path, minutes=8)
    extension.mode = ["provider-native"]
    extension.repetitions = 2
    extended = benchmark.run_benchmark(extension)
    assert [item["mode"] for item in calls] == [
        "provider-native",
        "provider-native",
    ]
    assert "output_comparison" in extended["runs"][0]

    calls.clear()
    with pytest.raises(ValueError, match="extend the frozen"):
        benchmark.run_benchmark(_args(report_path, language="en", minutes=32))
    assert calls == []


def test_orchestration_preserves_failure_and_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path, data, _samples_path, _value = _reference_tree(
        tmp_path, actual_samples={("zh", 8)}
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(benchmark, "REFERENCE_MANIFEST", manifest_path)
    monkeypatch.setattr(benchmark, "DATA_DIR", data)
    monkeypatch.setattr(benchmark, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(benchmark, "environment", lambda: {})
    monkeypatch.setattr(benchmark, "run_worker", _fake_worker(calls, fail_first=True))
    args = _args(tmp_path / "report.json", language="zh", minutes=8)

    first = benchmark.run_benchmark(args)
    assert first["runs"][0]["status"] == "failed"
    second = benchmark.run_benchmark(args)
    assert [item["attempt"] for item in second["runs"]] == [1, 2]
    assert second["runs"][1]["status"] == "succeeded"


def test_orchestration_rejects_schema2_and_reference_errors_before_environment_or_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path, data, _samples_path, value = _reference_tree(
        tmp_path, actual_samples={("zh", 8)}
    )
    calls: list[str] = []
    monkeypatch.setattr(benchmark, "REFERENCE_MANIFEST", manifest_path)
    monkeypatch.setattr(benchmark, "DATA_DIR", data)
    monkeypatch.setattr(benchmark, "environment", lambda: calls.append("environment"))
    monkeypatch.setattr(benchmark, "run_worker", lambda *_args: calls.append("worker"))
    args = _args(tmp_path / "report.json", language="zh", minutes=8)

    part = manifest_path.parent / value["languages"]["zh"]["parts"][0]["path"]
    part.write_text("damaged", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256"):
        benchmark.run_benchmark(args)
    assert calls == []

    write_json_atomic(args.report, {"schema_version": 2})
    with pytest.raises(ValueError, match="schema is obsolete"):
        benchmark.run_benchmark(args)
    assert calls == []


def test_report_rejects_policy_identity_and_reference_comparison_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path, data, _samples_path, _value = _reference_tree(
        tmp_path, actual_samples={("en", 8)}
    )
    monkeypatch.setattr(benchmark, "REFERENCE_MANIFEST", manifest_path)
    monkeypatch.setattr(benchmark, "DATA_DIR", data)
    monkeypatch.setattr(benchmark, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(benchmark, "environment", lambda: {})
    monkeypatch.setattr(benchmark, "run_worker", _fake_worker([]))
    args = _args(tmp_path / "report.json", language="en", minutes=8)
    benchmark.run_benchmark(args)
    pristine = json.loads(args.report.read_text(encoding="utf-8"))

    for mutation, message in [
        (
            lambda report: report["comparison_policy"].update({"id": "changed"}),
            "comparison policy",
        ),
        (
            lambda report: report["runs"][0].update({"run_id": "changed"}),
            "run_id",
        ),
        (
            lambda report: report["runs"][0]["reference_comparison"].update(
                {"edit_distance": 99}
            ),
            "error rate",
        ),
    ]:
        changed = deepcopy(pristine)
        mutation(changed)
        write_json_atomic(args.report, changed)
        with pytest.raises(ValueError, match=message):
            benchmark.run_benchmark(args)

    changed = deepcopy(pristine)
    changed["reference_set"]["manifest_sha256"] = "f" * 64
    write_json_atomic(args.report, changed)
    with pytest.raises(ValueError, match="reference identity"):
        benchmark.run_benchmark(args)


def test_freeze_reference_set_is_stably_sorted(tmp_path: Path) -> None:
    manifest_path, data, samples_path, _value = _reference_tree(tmp_path)
    manifest, samples = _load(
        manifest_path,
        data,
        samples_path,
        {("zh", 8), ("en", 16)},
    )

    frozen = freeze_reference_set(manifest, samples)

    assert [(item["language"], item["minutes"]) for item in frozen["samples"]] == [
        ("en", 16),
        ("zh", 8),
    ]
