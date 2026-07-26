from __future__ import annotations

import unicodedata

import pytest

from scripts.asr.alignment import (
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
    ("text", "item_texts"),
    [
        ("Hello world", ["Hello", "world"]),
        ("Hello,world", ["Helloworld"]),
        ("don't", ["don't"]),
        ("你好 A股", ["你好", "A股"]),
        ("state-of-the-art", ["stateoftheart"]),
        ("3.14", ["314"]),
        ("\tHello \n world\r\n", ["Hello", "world"]),
        ("，。!? --", []),
    ],
)
def test_alignment_items_validate_strictly_without_reproducing_tokenizer(
    text: str,
    item_texts: list[str],
) -> None:
    items = [AlignmentItem(item_text, 0.0, 0.0) for item_text in item_texts]

    assert (
        validate_alignment_contract(
            text,
            items,
            duration=0.0,
            chunk_index=0,
            language="en",
        )
        is None
    )


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


def test_all_configured_punctuation_creates_sentence_boundaries() -> None:
    text = "甲，乙,丙；丁;戊。己.庚！辛!壬？癸?"
    segments = build_sentence_segments(
        text,
        aligned(text, [float(index) for index in range(1, 11)]),
    )

    assert [segment["text"] for segment in segments] == [
        "甲，",
        "乙,",
        "丙；",
        "丁;",
        "戊。",
        "己.",
        "庚！",
        "辛!",
        "壬？",
        "癸?",
    ]
    assert [(segment["start"], segment["end"]) for segment in segments] == [
        (float(index), float(index + 1)) for index in range(10)
    ]


def test_short_sentences_are_not_merged() -> None:
    text = "你。好。"
    segments = build_sentence_segments(text, aligned(text, [1.0, 2.0]))

    assert segments == [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "你。"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "好。"},
    ]


def test_punctuation_inside_one_alignment_item_is_not_a_cut_point() -> None:
    text = "Hello.world"
    segments = build_sentence_segments(
        text,
        [AlignmentItem("Helloworld", 0.0, 12.0)],
    )

    assert segments == [{"id": 0, "start": 0.0, "end": 12.0, "text": text}]


def test_punctuation_at_end_of_alignment_item_is_a_cut_point() -> None:
    text = "Hello, world."
    segments = build_sentence_segments(
        text,
        [
            AlignmentItem("Hello,", 0.0, 1.0),
            AlignmentItem(" world.", 1.0, 2.0),
        ],
    )

    assert segments == [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "Hello,"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "world."},
    ]


def test_punctuation_only_cuts_after_the_alignment_item_is_fully_consumed() -> None:
    text = "Meet at a.m. Then leave."
    segments = build_sentence_segments(
        text,
        aligned(
            text,
            [1.0, 2.0, 3.0, 4.0, 5.0],
            item_texts=["Meet", "at", "am", "Then", "leave"],
        ),
    )

    assert segments == [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "Meet at a.m."},
        {"id": 1, "start": 3.0, "end": 5.0, "text": "Then leave."},
    ]


def test_unpunctuated_text_is_one_segment_regardless_of_length() -> None:
    words = [f"word{index}" for index in range(80)]
    text = " ".join(words)
    segments = build_sentence_segments(
        text,
        aligned(
            text,
            [float(index) for index in range(1, 81)],
            item_texts=words,
        ),
    )

    assert segments == [{"id": 0, "start": 0.0, "end": 80.0, "text": text}]


def test_trailing_text_without_punctuation_stays_in_one_segment() -> None:
    text = "第一句。没有标点的尾段"
    segments = build_sentence_segments(
        text,
        aligned(text, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]),
    )

    assert segments == [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "第一句。"},
        {"id": 1, "start": 3.0, "end": 10.0, "text": "没有标点的尾段"},
    ]


def test_consecutive_punctuation_does_not_create_empty_segments() -> None:
    text = "Really?! “Yes.”"
    segments = build_sentence_segments(
        text,
        aligned(
            text,
            [1.0, 2.0],
            item_texts=["Really", "Yes"],
        ),
    )

    assert segments == [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "Really?!"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "“Yes.”"},
    ]


def test_punctuation_only_text_has_no_segments() -> None:
    assert build_sentence_segments("，。!? --", []) == []
