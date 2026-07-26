from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

"""
合并结果是以标点符号分隔的句子
使用方可以自行合并为需要的粒度
"""

# 分隔标点符号
PUNCTUATION = set("，,；;。.!！？?")
# 时间容差
_TIME_EPSILON = 0.001


class AlignmentContractError(RuntimeError):
    """Provider text and word timestamps violate strict source mapping."""


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start: float
    end: float
    probability: float | None = None


AlignmentItem = TranscriptWord


def _to_float(value: Any) -> float:
    return round(float(value), 3)


def _is_skippable_source_character(char: str) -> bool:
    """
    判断是否是以下两种字符
    空格、换行、制表符等空白
    Unicode 分类为标点的字符
    """
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


def _walk_source_text(
    text: str,
    alignment_items: list[AlignmentItem],
    *,
    chunk_index: int | str,
    language: str,
    on_boundary: Callable[[int, int], None] | None = None,
) -> None:
    """
    遍历源文本
    按顺序消费时间戳项
    """
    for item_index, item in enumerate(alignment_items):
        # 校验每个时间戳项的文本不为空
        if item.text:
            continue
        raise _contract_error(
            chunk_index=chunk_index,
            language=language,
            token_index=item_index,
            expected="non-empty alignment text",
            actual=item.text,
            reason="empty alignment item",
        )

    # 当前处理第几个时间戳项
    item_index = 0
    # 当前处理该时间戳项的第几个字符
    item_char_index = 0
    pending_boundary: tuple[int, int] | None = None
    for source_index, actual_char in enumerate(text):
        expected_char = (
            alignment_items[item_index].text[item_char_index]
            if item_index < len(alignment_items)
            else None
        )
        if actual_char == expected_char:
            if pending_boundary is not None and item_char_index == 0:
                if on_boundary is not None:
                    # 触发上一个句子的边界回调
                    on_boundary(*pending_boundary)
                pending_boundary = None

            item_char_index += 1
            if item_char_index == len(alignment_items[item_index].text):
                item_index += 1
                item_char_index = 0
                if actual_char in PUNCTUATION:
                    pending_boundary = (source_index + 1, item_index)
            continue

        # 跳过源文本中不匹配的空白或标点符号
        if _is_skippable_source_character(actual_char):
            if actual_char in PUNCTUATION and item_char_index == 0:
                pending_boundary = (source_index + 1, item_index)
            continue

        if item_index >= len(alignment_items):
            raise _contract_error(
                chunk_index=chunk_index,
                language=language,
                token_index=item_index,
                expected=actual_char,
                actual="<end-of-alignment>",
                reason="source text has unmatched content",
            )
        raise _contract_error(
            chunk_index=chunk_index,
            language=language,
            token_index=item_index,
            expected=expected_char,
            actual=actual_char,
            reason="alignment text differs from source order",
        )

    if item_index < len(alignment_items):
        raise _contract_error(
            chunk_index=chunk_index,
            language=language,
            token_index=item_index,
            expected=alignment_items[item_index].text[item_char_index],
            actual="<end-of-text>",
            reason="alignment text continues after source text",
        )


def validate_alignment_contract(
    text: str,
    alignment_items: list[AlignmentItem],
    duration: float,
    *,
    chunk_index: int | str,
    language: str,
) -> None:
    """
    校验
    文本覆盖和顺序正确
    时间戳范围和顺序正确
    """
    _walk_source_text(
        text,
        alignment_items,
        chunk_index=chunk_index,
        language=language,
    )
    _validate_alignment_times(
        alignment_items,
        duration,
        chunk_index=chunk_index,
        language=language,
    )


def _validate_alignment_times(
    alignment_items: list[AlignmentItem],
    duration: float,
    *,
    chunk_index: int | str,
    language: str,
) -> None:
    """
    校验词级别时间戳合法性
    """
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


def _build_sentence_segments(
    text: str,
    alignment_items: list[AlignmentItem],
    *,
    chunk_index: int | str,
    language: str,
) -> list[dict[str, Any]]:
    if not alignment_items:
        return []

    segments: list[dict[str, Any]] = []
    # 当前句子在完整文本中的起始字符位置
    sentence_start_char = 0
    # 当前句子对应的第一个时间戳项
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
                    "start": _to_float(alignment_items[sentence_start_item].start),
                    "end": _to_float(alignment_items[end_item - 1].end),
                    "text": sentence_text,
                }
            )
        sentence_start_char = sentence_boundary
        sentence_start_item = end_item

    _walk_source_text(
        text,
        alignment_items,
        chunk_index=chunk_index,
        language=language,
        on_boundary=append_sentence,
    )

    sentence_text = text[sentence_start_char:].strip()
    if sentence_text:
        segments.append(
            {
                "id": len(segments),
                "start": _to_float(alignment_items[sentence_start_item].start),
                "end": _to_float(alignment_items[-1].end),
                "text": sentence_text,
            }
        )
    return segments


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
    segments = _build_sentence_segments(
        text,
        alignment_items,
        chunk_index=chunk_index,
        language=language,
    )
    _validate_alignment_times(
        alignment_items,
        maximum,
        chunk_index=chunk_index,
        language=language,
    )
    return segments
