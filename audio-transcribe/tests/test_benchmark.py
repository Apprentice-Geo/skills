from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from benchmark.prepare_audio import safe_cut
from scripts import benchmark
from scripts.benchmark import (
    build_matrix,
    compare_text,
    edit_distance,
    native_whisper_configuration,
    summarize,
)


def test_prepare_audio_module_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "benchmark.prepare_audio", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_safe_cut_uses_target_in_silence_and_speech_end_in_speech() -> None:
    intervals = [(100, 200), (300, 400)]

    assert safe_cut(250, intervals, 500) == 250
    assert safe_cut(150, intervals, 500) == 200
    assert safe_cut(200, intervals, 500) == 200
    with pytest.raises(ValueError, match="lookahead"):
        safe_cut(350, intervals, 375)


def test_matrix_alternates_modes_and_keeps_repetitions() -> None:
    matrix = build_matrix(["faster-whisper"], ["zh"], [8], repetitions=3)

    assert [(item["repetition"], item["mode"]) for item in matrix] == [
        (1, "project-slicing"),
        (1, "provider-native"),
        (2, "provider-native"),
        (2, "project-slicing"),
        (3, "project-slicing"),
        (3, "provider-native"),
    ]


def test_native_whisper_uses_entire_cpu_budget_for_one_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.asr.execution.whisper_cpu.os.cpu_count", lambda: 8)

    adapter, _policy, identity = native_whisper_configuration(16_000 * 60, "zh")

    assert adapter.options.cpu_threads == 6
    assert identity["cpu_budget"] == 6
    assert identity["cpu_threads"] == 6
    assert identity["num_workers"] == 1


def test_each_provider_warms_immediately_before_its_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, int]] = []

    def fake_run_worker(run: dict[str, object], *_args: object) -> dict[str, object]:
        calls.append((str(run["provider"]), int(run["repetition"])))
        return {
            **run,
            "status": "succeeded",
            "text": "",
            "run_id": benchmark.run_id(run),
            "wall_seconds": 1.0,
            "rtf": 0.1,
            "provider_stage_seconds": 0.5,
        }

    monkeypatch.setattr(benchmark, "run_worker", fake_run_worker)
    args = argparse.Namespace(
        report=tmp_path / "report.json",
        provider=["faster-whisper", "qwen3-asr"],
        language=["zh"],
        minutes=[8],
        mode=["project-slicing"],
        repetitions=1,
    )

    benchmark.run_benchmark(args)

    assert calls == [
        ("faster-whisper", 0),
        ("faster-whisper", 1),
        ("qwen3-asr", 0),
        ("qwen3-asr", 1),
    ]


def test_linear_memory_distance_and_language_normalization() -> None:
    assert edit_distance(list("kitten"), list("sitting")) == 3
    assert compare_text("你 好", "你好", "zh")["difference_rate"] == 0
    assert compare_text("Hello, WORLD!", "hello world", "en")["difference_rate"] == 0
    assert compare_text("", "", "en")["difference_rate"] is None
    comparison = compare_text("Ａ，臺灣！", "A 台湾", "zh")
    assert comparison["difference_rate"] == 0
    assert comparison["project_punctuation"] == 2
    assert comparison["native_punctuation"] == 0


def test_summary_pairs_repetitions_and_uses_comparison_median() -> None:
    runs = []
    for repetition, project, native in [
        (1, "甲", "甲"),
        (2, "乙", "甲"),
        (3, "丙。", "甲!"),
    ]:
        comparison = compare_text(project, native, "zh")
        for mode, text in [("project-slicing", project), ("provider-native", native)]:
            run = {
                "status": "succeeded",
                "provider": "faster-whisper",
                "language": "zh",
                "minutes": 8,
                "mode": mode,
                "repetition": repetition,
                "text": text,
                "wall_seconds": 1.0,
                "rtf": 0.1,
                "provider_stage_seconds": 0.5,
            }
            if mode == "project-slicing":
                run["output_comparison"] = comparison
            runs.append(run)

    markdown = summarize({"runs": runs})

    assert "| zh | 8 | project-slicing" in markdown
    assert "100.000%" in markdown
    assert "| zh | 8 | provider-native" in markdown
    assert markdown.count("100.000%") == 1
