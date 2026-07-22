from __future__ import annotations

import logging
import unicodedata

import pytest

from scripts.asr.qwen3_alignment import (
    AlignmentContractError,
    AlignmentItem,
    build_sentence_segments,
    validate_alignment_contract,
)


def aligned(
    text: str,
    ends: list[float],
    *,
    item_texts: list[str] | None = None,
    starts: list[float] | None = None,
) -> list[AlignmentItem]:
    item_texts = item_texts or [
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    ]
    starts = starts or [0.0, *ends[:-1]]
    return [
        AlignmentItem(item_text, start, end)
        for item_text, start, end in zip(item_texts, starts, ends, strict=True)
    ]


@pytest.mark.parametrize(
    ("text", "item_texts", "expected_spans"),
    [
        ("Hello world", ["Hello", "world"], [("Hello", 0, 5), ("world", 6, 11)]),
        ("Hello,world", ["Helloworld"], [("Helloworld", 0, 11)]),
        ("don't", ["don't"], [("don't", 0, 5)]),
        ("你好 A股", ["你好", "A股"], [("你好", 0, 2), ("A股", 3, 5)]),
        (
            "state-of-the-art",
            ["stateoftheart"],
            [("stateoftheart", 0, 16)],
        ),
        ("3.14", ["314"], [("314", 0, 4)]),
        (
            "\tHello \n world\r\n",
            ["Hello", "world"],
            [("Hello", 1, 6), ("world", 9, 14)],
        ),
        ("，。!? --", [], []),
    ],
)
def test_alignment_items_map_strictly_to_source_without_reproducing_tokenizer(
    text: str,
    item_texts: list[str],
    expected_spans: list[tuple[str, int, int]],
) -> None:
    items = [AlignmentItem(item_text, 0.0, 0.0) for item_text in item_texts]

    spans = validate_alignment_contract(
        text,
        items,
        duration=0.0,
        chunk_index=0,
        language="en",
    )

    assert [
        (span.text, span.start_char, span.end_char) for span in spans
    ] == expected_spans


@pytest.mark.parametrize(
    "items",
    [
        [AlignmentItem("hello", 0.0, 1.0)],
        [AlignmentItem("Hello", 0.0, 1.0)],
        [AlignmentItem("Hello", 0.0, 1.0), AlignmentItem("there", 1.0, 2.0)],
        [AlignmentItem("world", 0.0, 1.0), AlignmentItem("Hello", 1.0, 2.0)],
        [
            AlignmentItem("Hello", 0.0, 1.0),
            AlignmentItem("world", 1.0, 2.0),
            AlignmentItem("again", 2.0, 3.0),
        ],
        [AlignmentItem("", 0.0, 1.0)],
        [AlignmentItem("Hello", 0.0, 1.0), AlignmentItem("world", 0.5, 2.0)],
        [AlignmentItem("Hello", float("nan"), 1.0), AlignmentItem("world", 1.0, 2.0)],
        [AlignmentItem("Hello", 0.0, 1.0), AlignmentItem("world", 1.0, 3.1)],
    ],
)
def test_validate_alignment_contract_rejects_text_coverage_order_and_time(
    items: list[AlignmentItem],
) -> None:
    with pytest.raises(AlignmentContractError) as exc_info:
        validate_alignment_contract(
            "Hello world",
            items,
            duration=3.0,
            chunk_index=7,
            language="en",
        )

    message = str(exc_info.value)
    assert "chunk=7" in message
    assert "language=en" in message
    assert "token_index=" in message
    assert "expected=" in message
    assert "actual=" in message


def test_strong_punctuation_keeps_short_semantic_sentences() -> None:
    text = "你。好。"
    segments = build_sentence_segments(text, aligned(text, [1.0, 2.0]))

    assert [segment["text"] for segment in segments] == ["你。", "好。"]
    assert [(segment["start"], segment["end"]) for segment in segments] == [
        (0.0, 1.0),
        (1.0, 2.0),
    ]


def test_punctuation_inside_one_alignment_item_is_not_a_cut_point() -> None:
    text = "Hello.world"
    segments = build_sentence_segments(
        text,
        [AlignmentItem("Helloworld", 0.0, 12.0)],
    )

    assert segments == [{"id": 0, "start": 0.0, "end": 12.0, "text": text}]


def test_unpunctuated_sentence_uses_real_token_times_near_ten_seconds() -> None:
    text = "甲乙丙丁戊己庚辛壬癸"
    segments = build_sentence_segments(
        text, aligned(text, [float(i) for i in range(2, 22, 2)])
    )

    assert [segment["text"] for segment in segments] == ["甲乙丙丁戊", "己庚辛壬癸"]
    assert [(segment["start"], segment["end"]) for segment in segments] == [
        (0.0, 10.0),
        (10.0, 20.0),
    ]


def test_equal_distance_prefers_weak_punctuation_then_space() -> None:
    weak_text = "aa bb, cc"
    weak_segments = build_sentence_segments(
        weak_text,
        aligned(weak_text, [4.0, 8.0, 12.0], item_texts=["aa", "bb", "cc"]),
    )
    space_text = "甲乙 cc"
    space_segments = build_sentence_segments(
        space_text,
        aligned(space_text, [4.0, 8.0, 12.0], item_texts=["甲", "乙", "cc"]),
    )

    assert [segment["text"] for segment in weak_segments] == ["aa bb,", "cc"]
    assert [segment["text"] for segment in space_segments] == ["甲乙", "cc"]


def test_short_tail_merges_backward_without_crossing_hard_limit() -> None:
    text = "甲乙丙丁戊己"
    segments = build_sentence_segments(
        text, aligned(text, [2.0, 4.0, 6.0, 8.0, 10.0, 12.0])
    )

    assert segments == [{"id": 0, "start": 0.0, "end": 12.0, "text": text}]


def test_character_hard_limit_splits_only_at_token_boundaries() -> None:
    text = "甲" * 60
    segments = build_sentence_segments(
        text,
        aligned(text, [round((index + 1) / 10, 1) for index in range(60)]),
    )

    assert [len(segment["text"]) for segment in segments] == [50, 10]
    assert "".join(segment["text"] for segment in segments) == text
    assert [(segment["start"], segment["end"]) for segment in segments] == [
        (0.0, 5.0),
        (5.0, 6.0),
    ]


def test_overlong_atomic_token_is_kept_and_warned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = "a" * 51

    with caplog.at_level(logging.WARNING):
        segments = build_sentence_segments(
            text,
            [AlignmentItem(text, 0.0, 21.0)],
        )

    assert segments == [{"id": 0, "start": 0.0, "end": 21.0, "text": text}]
    assert "single Qwen3 token exceeds segmentation hard limit" in caplog.text
