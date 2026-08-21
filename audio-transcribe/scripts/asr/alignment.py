from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Final

PUNCTUATION: Final = frozenset("，,；;。.!！？?")
ALIGNMENT_POLICY: Final = {
    "schema_version": 1,
    "timestamp_resolution_ms": 1,
    "zero_duration": "drop_item_and_owned_text",
    "ordering": "strict",
}


class AlignmentContractError(RuntimeError):
    """Provider text and timestamp items violate the alignment contract."""


@dataclass(frozen=True)
class AlignmentItem:
    text: str
    start: float
    end: float
    probability: float | None = None


@dataclass(frozen=True)
class AlignedTranscript:
    text: str
    items: tuple[AlignmentItem, ...]


@dataclass(frozen=True)
class CleanupReport:
    dropped_zero_duration_items: int = 0
    first_start: float | None = None
    last_end: float | None = None


# Transitional aliases for pipeline callers. There is only one item definition.
TranscriptWord = AlignmentItem


def quantize_timestamp(value: Any) -> float:
    return round(float(value), 3)


_to_float = quantize_timestamp


def _is_skippable_source_character(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith("P")


def _contract_error(
    *,
    chunk_index: int | str,
    language: str,
    item_index: int,
    expected: Any,
    actual: Any,
    reason: str,
) -> AlignmentContractError:
    return AlignmentContractError(
        "ASR alignment contract mismatch: "
        f"chunk={chunk_index}, language={language}, item_index={item_index}, "
        f"expected={expected!r}, actual={actual!r}, reason={reason}"
    )


def _walk_source_text(
    alignment: AlignedTranscript,
    *,
    chunk_index: int | str,
    language: str,
    on_boundary: Callable[[int, int], None] | None = None,
) -> tuple[int | None, ...]:
    for item_index, item in enumerate(alignment.items):
        if not item.text:
            raise _contract_error(
                chunk_index=chunk_index,
                language=language,
                item_index=item_index,
                expected="non-empty alignment text",
                actual=item.text,
                reason="empty alignment item",
            )

    owners: list[int | None] = [None] * len(alignment.text)
    item_index = 0
    item_char_index = 0
    pending_boundary: tuple[int, int] | None = None
    for source_index, actual_char in enumerate(alignment.text):
        expected_char = (
            alignment.items[item_index].text[item_char_index]
            if item_index < len(alignment.items)
            else None
        )
        if actual_char == expected_char:
            if pending_boundary is not None and item_char_index == 0:
                if on_boundary is not None:
                    on_boundary(*pending_boundary)
                pending_boundary = None
            owners[source_index] = item_index
            item_char_index += 1
            if item_char_index == len(alignment.items[item_index].text):
                item_index += 1
                item_char_index = 0
                if actual_char in PUNCTUATION:
                    pending_boundary = (source_index + 1, item_index)
            continue

        if _is_skippable_source_character(actual_char):
            if actual_char in PUNCTUATION and item_char_index == 0:
                pending_boundary = (source_index + 1, item_index)
            continue
        if item_index >= len(alignment.items):
            raise _contract_error(
                chunk_index=chunk_index,
                language=language,
                item_index=item_index,
                expected=actual_char,
                actual="<end-of-alignment>",
                reason="source text has unmatched content",
            )
        raise _contract_error(
            chunk_index=chunk_index,
            language=language,
            item_index=item_index,
            expected=expected_char,
            actual=actual_char,
            reason="alignment text differs from source order",
        )

    if item_index < len(alignment.items):
        raise _contract_error(
            chunk_index=chunk_index,
            language=language,
            item_index=item_index,
            expected=alignment.items[item_index].text[item_char_index],
            actual="<end-of-text>",
            reason="alignment text continues after source text",
        )
    return tuple(owners)


def source_character_owners(
    alignment: AlignedTranscript,
    *,
    chunk_index: int | str = "alignment",
    language: str = "unknown",
) -> tuple[int | None, ...]:
    """Map source characters to timestamp items without guessing punctuation owners."""
    return _walk_source_text(
        alignment,
        chunk_index=chunk_index,
        language=language,
    )


def sentence_boundaries(
    alignment: AlignedTranscript,
    *,
    chunk_index: int | str = "merged",
    language: str = "unknown",
) -> tuple[tuple[int, int], ...]:
    boundaries: list[tuple[int, int]] = []
    _walk_source_text(
        alignment,
        chunk_index=chunk_index,
        language=language,
        on_boundary=lambda source_index, item_index: boundaries.append(
            (source_index, item_index)
        ),
    )
    return tuple(boundaries)


def validate_alignment(
    alignment: AlignedTranscript,
    duration: float,
    *,
    chunk_index: int | str = "alignment",
    language: str = "unknown",
) -> None:
    if not math.isfinite(duration) or duration < 0:
        raise AlignmentContractError(
            "Alignment duration must be finite and non-negative."
        )
    _walk_source_text(
        alignment,
        chunk_index=chunk_index,
        language=language,
    )
    previous_end = 0.0
    for index, item in enumerate(alignment.items):
        valid = (
            math.isfinite(item.start)
            and math.isfinite(item.end)
            and item.start >= 0.0
            and item.start >= previous_end
            and item.end > item.start
            and item.end <= duration
            and (
                item.probability is None
                or (math.isfinite(item.probability) and 0.0 <= item.probability <= 1.0)
            )
        )
        if not valid:
            raise _contract_error(
                chunk_index=chunk_index,
                language=language,
                item_index=index,
                expected=f"finite ordered time satisfying 0 <= start < end <= {duration}",
                actual=item,
                reason="invalid timestamp item",
            )
        previous_end = item.end


def validate_alignment_contract(
    text: str,
    alignment_items: list[AlignmentItem],
    duration: float,
    *,
    chunk_index: int | str,
    language: str,
) -> None:
    """Compatibility wrapper while pipeline callers move to AlignedTranscript."""
    validate_alignment(
        AlignedTranscript(text, tuple(alignment_items)),
        duration,
        chunk_index=chunk_index,
        language=language,
    )


def quantize_alignment(alignment: AlignedTranscript) -> AlignedTranscript:
    return AlignedTranscript(
        alignment.text,
        tuple(
            AlignmentItem(
                item.text,
                quantize_timestamp(item.start),
                quantize_timestamp(item.end),
                item.probability,
            )
            for item in alignment.items
        ),
    )


def offset_alignment(alignment: AlignedTranscript, offset: float) -> AlignedTranscript:
    return AlignedTranscript(
        alignment.text,
        tuple(
            AlignmentItem(
                item.text,
                quantize_timestamp(offset + item.start),
                quantize_timestamp(offset + item.end),
                item.probability,
            )
            for item in alignment.items
        ),
    )


def project_normalized_text(
    alignment: AlignedTranscript,
    normalized_text: str,
    *,
    chunk_index: int | str = "normalization",
    language: str = "unknown",
) -> AlignedTranscript:
    owners = source_character_owners(
        alignment,
        chunk_index=chunk_index,
        language=language,
    )
    fragments: list[tuple[str, object, float, float, float | None]] = []

    def append(text: str, owner_indexes: list[int]) -> None:
        if not text or not owner_indexes:
            return
        unique_owners = list(dict.fromkeys(owner_indexes))
        owner_items = [alignment.items[index] for index in unique_owners]
        probabilities = [
            item.probability for item in owner_items if item.probability is not None
        ]
        key: object = (
            ("item", unique_owners[0])
            if len(unique_owners) == 1
            else ("group", tuple(unique_owners))
        )
        fragment = (
            text,
            key,
            owner_items[0].start,
            owner_items[-1].end,
            min(probabilities) if probabilities else None,
        )
        if fragments and fragments[-1][1:] == fragment[1:]:
            previous = fragments[-1]
            fragments[-1] = (previous[0] + text, *previous[1:])
        else:
            fragments.append(fragment)

    def adjacent_owner(position: int) -> int | None:
        return next(
            (owner for owner in reversed(owners[:position]) if owner is not None),
            next((owner for owner in owners[position:] if owner is not None), None),
        )

    for (
        tag,
        source_start,
        source_end,
        normalized_start,
        normalized_end,
    ) in SequenceMatcher(
        None, alignment.text, normalized_text, autojunk=False
    ).get_opcodes():
        replacement = normalized_text[normalized_start:normalized_end]
        if tag == "equal" or (
            tag == "replace"
            and source_end - source_start == normalized_end - normalized_start
        ):
            for offset, char in enumerate(replacement):
                owner = owners[source_start + offset]
                if owner is not None:
                    append(char, [owner])
        elif tag == "replace":
            affected = [
                owner for owner in owners[source_start:source_end] if owner is not None
            ]
            if affected:
                append(replacement, affected)
            else:
                owner = adjacent_owner(source_start)
                if owner is not None:
                    append(
                        "".join(
                            char
                            for char in replacement
                            if not _is_skippable_source_character(char)
                        ),
                        [owner],
                    )
        elif tag == "insert":
            owner = adjacent_owner(source_start)
            if owner is not None:
                append(
                    "".join(
                        char
                        for char in replacement
                        if not _is_skippable_source_character(char)
                    ),
                    [owner],
                )

    return AlignedTranscript(
        normalized_text,
        tuple(
            AlignmentItem(text, start, end, probability)
            for text, _, start, end, probability in fragments
        ),
    )
