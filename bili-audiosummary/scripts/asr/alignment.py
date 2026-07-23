from __future__ import annotations

import logging
import math
import unicodedata
from dataclasses import dataclass
from typing import Any

STRONG_PUNCTUATION = set("。.!！？?")
WEAK_PUNCTUATION = set("，,；;")
# 最小句子时长
MIN_SEGMENT_SECONDS = 2.0
<<<<<<< HEAD
# 目标句子时长
TARGET_SEGMENT_SECONDS = 12.0
=======
>>>>>>> feat/bili-audiosummary
# 最大句子时长
MAX_SEGMENT_SECONDS = 24.0
# 最大句子对齐项数
MAX_SEGMENT_ALIGNMENT_ITEMS = 72
# 时间容差
_TIME_EPSILON = 0.001

logger = logging.getLogger(__name__)


class AlignmentContractError(RuntimeError):
    """Provider text and word timestamps violate strict source mapping."""


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start: float
    end: float
    probability: float | None = None


AlignmentItem = TranscriptWord


@dataclass(frozen=True)
class AlignedTextSpan:
    text: str
    start_char: int
    end_char: int


@dataclass
class _SegmentPiece:
    start_token: int
    end_token: int
    start_char: int
    end_char: int


@dataclass(frozen=True)
class _SemanticInterval:
    start_token: int
    end_token: int
    start_char: int
    end_char: int


def _to_float(value: Any) -> float:
    return round(float(value), 3)


def _is_skippable_source_character(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith("P")


def _contract_error(
    *,
    chunk_index: int | str,
    language: str,
    token_index: int,
    expected: Any,
    actual: Any,
    reason: str,
) -> AlignmentContractError:
    return AlignmentContractError(
        "ASR alignment contract mismatch: "
        f"chunk={chunk_index}, language={language}, token_index={token_index}, "
        f"expected={expected!r}, actual={actual!r}, reason={reason}"
    )


def validate_alignment_contract(
    text: str,
    alignment_items: list[AlignmentItem],
    duration: float,
    *,
    chunk_index: int | str,
    language: str,
) -> list[AlignedTextSpan]:
    spans: list[AlignedTextSpan] = []
    source_index = 0
    for item_index, item in enumerate(alignment_items):
        if not item.text:
            raise _contract_error(
                chunk_index=chunk_index,
                language=language,
                token_index=item_index,
                expected="non-empty alignment text",
                actual=item.text,
                reason="empty alignment item",
            )
        first_match: int | None = None
        last_match: int | None = None
        for expected_char in item.text:
            while source_index < len(text) and text[source_index] != expected_char:
                actual_char = text[source_index]
                if not _is_skippable_source_character(actual_char):
                    raise _contract_error(
                        chunk_index=chunk_index,
                        language=language,
                        token_index=item_index,
                        expected=expected_char,
                        actual=actual_char,
                        reason="alignment text differs from source order",
                    )
                source_index += 1
            if source_index >= len(text):
                raise _contract_error(
                    chunk_index=chunk_index,
                    language=language,
                    token_index=item_index,
                    expected=expected_char,
                    actual="<end-of-text>",
                    reason="alignment text continues after source text",
                )
            if first_match is None:
                first_match = source_index
            last_match = source_index + 1
            source_index += 1
        assert first_match is not None and last_match is not None
        spans.append(AlignedTextSpan(item.text, first_match, last_match))

    while source_index < len(text) and _is_skippable_source_character(
        text[source_index]
    ):
        source_index += 1
    if source_index < len(text):
        raise _contract_error(
            chunk_index=chunk_index,
            language=language,
            token_index=len(alignment_items),
            expected=text[source_index],
            actual="<end-of-alignment>",
            reason="source text has unmatched content",
        )

    previous_end = 0.0
    for index, item in enumerate(alignment_items):
        times = (item.start, item.end)
        valid = (
            all(math.isfinite(value) for value in times)
            and item.start >= 0.0
            and item.start + _TIME_EPSILON >= previous_end
            and item.end + _TIME_EPSILON >= item.start
            and item.end <= duration + _TIME_EPSILON
        )
        if not valid:
            raise _contract_error(
                chunk_index=chunk_index,
                language=language,
                token_index=index,
                expected=f"finite monotonic time in [0, {duration}]",
                actual={"text": item.text, "start": item.start, "end": item.end},
                reason="invalid token time",
            )
        previous_end = item.end
    return spans


def normalize_alignment_items(items: list[Any]) -> list[TranscriptWord]:
    normalized: list[TranscriptWord] = []
    for item in items:
        text = str(getattr(item, "text", "") or "")
        start = getattr(item, "start_time", None)
        end = getattr(item, "end_time", None)
        if start is None or end is None:
            continue
        normalized.append(
            TranscriptWord(text=text, start=_to_float(start), end=_to_float(end))
        )
    return normalized


def _piece_alignment_item_count(piece: _SegmentPiece) -> int:
    return piece.end_token - piece.start_token


def _boundary_rank(text: str, tokens: list[AlignedTextSpan], boundary: int) -> int:
    """
    确认切分优先级
    越小越优先
    """
    if boundary >= len(tokens):
        return 3
    gap = text[
        max(tokens[boundary - 1].end_char - 1, 0) : min(
            tokens[boundary].start_char + 1, len(text)
        )
    ]
    if any(char in STRONG_PUNCTUATION for char in gap):
        return 0
    if any(char in WEAK_PUNCTUATION for char in gap):
        return 1
    if any(char.isspace() for char in gap):
        return 2
    return 3


def _semantic_intervals(
    text: str,
    tokens: list[AlignedTextSpan],
    alignment_items: list[AlignmentItem],
) -> list[_SemanticInterval]:
    intervals: list[_SemanticInterval] = []
    start_token = 0
    start_char = 0
    for boundary in range(1, len(tokens) + 1):
        gap_end = tokens[boundary].start_char if boundary < len(tokens) else len(text)
        # 前后两个 token 的边界严格相等
        # 因此需要 -1 和 +1 来获取首尾可能的标点符号
        gap = text[
            max(tokens[boundary - 1].end_char - 1, 0) : min(gap_end + 1, len(text))
        ]
        if not any(char in STRONG_PUNCTUATION for char in gap):
            continue
        duration = (
            alignment_items[boundary - 1].end - alignment_items[start_token].start
        )

        # 强标点不再允许产生过短句
        if duration + _TIME_EPSILON < MIN_SEGMENT_SECONDS:
            continue
        intervals.append(_SemanticInterval(start_token, boundary, start_char, gap_end))
        start_token = boundary
        start_char = gap_end

    # 剩余尾段
    if start_token < len(tokens):
        tail = _SemanticInterval(
            start_token=start_token,
            end_token=len(tokens),
            start_char=start_char,
            end_char=len(text),
        )

        tail_duration = alignment_items[-1].end - alignment_items[start_token].start

        # 尾段不足最短时长时，与前一区间合并
        if intervals and tail_duration + _TIME_EPSILON < MIN_SEGMENT_SECONDS:
            previous = intervals[-1]
            intervals[-1] = _SemanticInterval(
                start_token=previous.start_token,
                end_token=tail.end_token,
                start_char=previous.start_char,
                end_char=tail.end_char,
            )
        else:
            intervals.append(tail)
    return intervals


def _piece_text(text: str, piece: _SegmentPiece) -> str:
    return text[piece.start_char : piece.end_char].strip()


def _piece_duration(
    piece: _SegmentPiece, alignment_items: list[AlignmentItem]
) -> float:
    return max(
        0.0,
        alignment_items[piece.end_token - 1].end
        - alignment_items[piece.start_token].start,
    )


def _within_hard_limits(
    piece: _SegmentPiece,
    alignment_items: list[AlignmentItem],
) -> bool:
    return (
        _piece_duration(piece, alignment_items) <= MAX_SEGMENT_SECONDS + _TIME_EPSILON
        and _piece_alignment_item_count(piece) <= MAX_SEGMENT_ALIGNMENT_ITEMS
    )


def _split_interval(
    text: str,
    tokens: list[AlignedTextSpan],
    alignment_items: list[AlignmentItem],
    interval: _SemanticInterval,
) -> list[_SegmentPiece]:
    pieces: list[_SegmentPiece] = []
    start = interval.start_token
    while start < interval.end_token:
        start_char = (
            interval.start_char
            if start == interval.start_token
            else tokens[start].start_char
        )
        candidates: list[tuple[int, int, _SegmentPiece]] = []
        for boundary in range(start + 1, interval.end_token + 1):
            end_char = (
                interval.end_char
                if boundary == interval.end_token
                else tokens[boundary].start_char
            )
            piece = _SegmentPiece(start, boundary, start_char, end_char)
            if not _within_hard_limits(piece, alignment_items):
                continue
            candidates.append(
                (
                    _boundary_rank(text, tokens, boundary),
                    boundary,
                    piece,
                )
            )

        if not candidates:
            end_char = (
                interval.end_char
                if start + 1 == interval.end_token
                else tokens[start + 1].start_char
            )
            chosen = _SegmentPiece(start, start + 1, start_char, end_char)
            logger.warning(
                "single ASR alignment item exceeds segmentation hard limit: "
                "item=%r duration=%.3fs alignment_items=%d",
                tokens[start].text,
                _piece_duration(chosen, alignment_items),
                _piece_alignment_item_count(chosen),
            )
        else:
            soft_candidates = [
                candidate
                for candidate in candidates
                if _piece_duration(candidate[2], alignment_items) + _TIME_EPSILON
                >= MIN_SEGMENT_SECONDS
            ]
            # 先比较优先级 再比较长度 都取最小的
            chosen = min(
                soft_candidates or candidates, key=lambda item: (item[0], item[1])
            )[2]
        pieces.append(chosen)
        start = chosen.end_token

    if (
        len(pieces) > 1
        and _piece_duration(pieces[-1], alignment_items) < MIN_SEGMENT_SECONDS
    ):
        previous = pieces[-2]
        tail = pieces[-1]
        combined = _SegmentPiece(
            previous.start_token,
            tail.end_token,
            previous.start_char,
            tail.end_char,
        )
        if _within_hard_limits(combined, alignment_items):
            pieces[-2:] = [combined]
    return pieces


def build_sentence_segments(
    text: str,
    alignment_items: list[AlignmentItem],
    duration: float | None = None,
    *,
    chunk_index: int | str = "merged",
    language: str = "unknown",
) -> list[dict[str, Any]]:
    maximum = (
        duration
        if duration is not None
        else (alignment_items[-1].end if alignment_items else 0.0)
    )
    tokens = validate_alignment_contract(
        text,
        alignment_items,
        maximum,
        chunk_index=chunk_index,
        language=language,
    )
    pieces = [
        piece
        for interval in _semantic_intervals(text, tokens, alignment_items)
        for piece in _split_interval(text, tokens, alignment_items, interval)
    ]
    return [
        {
            "id": index,
            "start": _to_float(alignment_items[piece.start_token].start),
            "end": _to_float(alignment_items[piece.end_token - 1].end),
            "text": _piece_text(text, piece),
        }
        for index, piece in enumerate(pieces)
        if _piece_text(text, piece)
    ]
