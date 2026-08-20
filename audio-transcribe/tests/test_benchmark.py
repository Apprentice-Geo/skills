from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from benchmark.prepare_audio import safe_cut
from scripts.benchmark import build_matrix, compare_text, edit_distance


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


def test_linear_memory_distance_and_language_normalization() -> None:
    assert edit_distance(list("kitten"), list("sitting")) == 3
    assert compare_text("你 好", "你好", "zh")["difference_rate"] == 0
    assert compare_text("Hello, WORLD!", "hello world", "en")["difference_rate"] == 0
    assert compare_text("", "", "en")["difference_rate"] is None
