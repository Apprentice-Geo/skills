from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# A punctuation mark in this set ends a public transcript segment immediately.
PUNCTUATION = set("，,；;。.!！？?")
_TIME_EPSILON = 0.001


class AlignmentContractError(RuntimeError):
    """Provider text and timestamp items violate strict source mapping."""


@dataclass(frozen=True)
class AlignmentItem:
    text: str
    start: float
    end: float
    probability: float | None = None


def _to_float(value: Any) -> float:
    return round(float(value), 3)


def _is_skippable_source_character(char: str) -> bool:
    """Return whether source mapping may skip whitespace or punctuation."""
    return char.isspace() or unicodedata.category(char).startswith("P")


def _contract_error(
    *,
    item_index: int,
    expected: Any,
    actual: Any,
    reason: str,
) -> AlignmentContractError:
    return AlignmentContractError(
        "ASR alignment contract mismatch: "
        f"item_index={item_index}, expected={expected!r}, "
        f"actual={actual!r}, reason={reason}"
    )


def _walk_source_text(
    text: str,
    items: list[AlignmentItem],
    on_boundary: Callable[[int, int], None] | None = None,
) -> None:
    """Consume timestamp items in source order and expose sentence boundaries."""
    for index, item in enumerate(items):
        if not item.text:
            raise _contract_error(
                item_index=index,
                expected="non-empty alignment text",
                actual=item.text,
                reason="empty alignment item",
            )

    item_index = 0
    item_char_index = 0
    pending_boundary: tuple[int, int] | None = None
    for source_index, actual_char in enumerate(text):
        expected_char = (
            items[item_index].text[item_char_index] if item_index < len(items) else None
        )
        if actual_char == expected_char:
            if pending_boundary is not None and item_char_index == 0:
                if on_boundary is not None:
                    on_boundary(*pending_boundary)
                pending_boundary = None
            item_char_index += 1
            if item_char_index == len(items[item_index].text):
                item_index += 1
                item_char_index = 0
                if actual_char in PUNCTUATION:
                    pending_boundary = (source_index + 1, item_index)
            continue

        # Providers may omit punctuation or whitespace from timestamp items, but
        # their actual text must still map to the original transcript in order.
        if _is_skippable_source_character(actual_char):
            if actual_char in PUNCTUATION and item_char_index == 0:
                pending_boundary = (source_index + 1, item_index)
            continue
        if item_index >= len(items):
            raise _contract_error(
                item_index=item_index,
                expected=actual_char,
                actual="<end-of-alignment>",
                reason="source text has unmatched content",
            )
        raise _contract_error(
            item_index=item_index,
            expected=expected_char,
            actual=actual_char,
            reason="alignment text differs from source order",
        )

    if item_index < len(items):
        raise _contract_error(
            item_index=item_index,
            expected=items[item_index].text[item_char_index],
            actual="<end-of-text>",
            reason="alignment text continues after source text",
        )


def validate_alignment(text: str, items: list[AlignmentItem], duration: float) -> None:
    _walk_source_text(text, items)
    previous_end = 0.0
    for index, item in enumerate(items):
        valid = (
            math.isfinite(item.start)
            and math.isfinite(item.end)
            and item.start >= 0.0
            and item.start + _TIME_EPSILON >= previous_end
            and item.end + _TIME_EPSILON >= item.start
            and item.end <= duration + _TIME_EPSILON
            and (
                item.probability is None
                or (math.isfinite(item.probability) and 0.0 <= item.probability <= 1.0)
            )
        )
        if not valid:
            raise _contract_error(
                item_index=index,
                expected=f"finite monotonic time in [0, {duration}]",
                actual=item,
                reason="invalid timestamp item",
            )
        previous_end = item.end


def build_sentence_segments(
    text: str, items: list[AlignmentItem], duration: float
) -> list[dict[str, Any]]:
    validate_alignment(text, items, duration)
    if not items:
        return []

    segments: list[dict[str, Any]] = []
    sentence_start_char = 0
    sentence_start_item = 0

    def append_sentence(sentence_boundary: int, end_item: int) -> None:
        nonlocal sentence_start_char, sentence_start_item
        if end_item == sentence_start_item:
            sentence_start_char = sentence_boundary
            return
        sentence_text = text[sentence_start_char:sentence_boundary].strip()
        if sentence_text:
            segments.append(
                {
                    "id": len(segments),
                    "start": _to_float(items[sentence_start_item].start),
                    "end": _to_float(items[end_item - 1].end),
                    "text": sentence_text,
                }
            )
        sentence_start_char = sentence_boundary
        sentence_start_item = end_item

    _walk_source_text(text, items, append_sentence)
    sentence_text = text[sentence_start_char:].strip()
    if sentence_text:
        segments.append(
            {
                "id": len(segments),
                "start": _to_float(items[sentence_start_item].start),
                "end": _to_float(items[-1].end),
                "text": sentence_text,
            }
        )
    return segments
