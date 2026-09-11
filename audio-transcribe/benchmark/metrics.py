from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from scripts.text_normalization import (
    TEXT_NORMALIZATION_POLICY,
    normalize_transcript_text,
)

COMPARISON_POLICY = {
    "id": "benchmark-reference-v1",
    "text_normalization": TEXT_NORMALIZATION_POLICY,
    "zh_units": "remove Unicode whitespace and punctuation after NFKC and OpenCC t2s",
    "en_units": "Unicode words after NFKC and casefold; preserve internal ' and ’",
    "punctuation": "count Unicode punctuation after language normalization",
    "mode_pairing": "pair project-slicing and provider-native by repetition; provider-native is the denominator",
}


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
