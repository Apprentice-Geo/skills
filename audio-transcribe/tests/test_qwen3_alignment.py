from __future__ import annotations

import unicodedata

import pytest

from scripts.asr.alignment import (
    AlignedTranscript,
    AlignmentContractError,
    AlignmentItem,
    CleanupReport,
    accept_provider_transcript,
    source_character_owners,
    validate_alignment_contract,
)
from scripts.asr.segmentation import build_sentence_segments


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


def segmented(text: str, items: list[AlignmentItem]) -> list[dict[str, object]]:
    return build_sentence_segments(AlignedTranscript(text, tuple(items)))


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
    items = [
        AlignmentItem(item_text, index / 10, (index + 1) / 10)
        for index, item_text in enumerate(item_texts)
    ]

    assert (
        validate_alignment_contract(
            text,
            items,
            duration=len(items) / 10,
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
        [AlignmentItem("Hello", 0.0, 1.0, 1.01), AlignmentItem("world", 1.0, 2.0)],
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
    assert "item_index=" in message
    assert "expected=" in message
    assert "actual=" in message


def test_all_configured_punctuation_creates_sentence_boundaries() -> None:
    text = "甲，乙,丙；丁;戊。己.庚！辛!壬？癸?"
    segments = segmented(
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
    segments = segmented(text, aligned(text, [1.0, 2.0]))

    assert segments == [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "你。"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "好。"},
    ]


def test_punctuation_inside_one_alignment_item_is_not_a_cut_point() -> None:
    text = "Hello.world"
    segments = segmented(
        text,
        [AlignmentItem("Helloworld", 0.0, 12.0)],
    )

    assert segments == [{"id": 0, "start": 0.0, "end": 12.0, "text": text}]


def test_punctuation_at_end_of_alignment_item_is_a_cut_point() -> None:
    text = "Hello, world."
    segments = segmented(
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
    segments = segmented(
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
    segments = segmented(
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
    segments = segmented(
        text,
        aligned(text, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]),
    )

    assert segments == [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "第一句。"},
        {"id": 1, "start": 3.0, "end": 10.0, "text": "没有标点的尾段"},
    ]


def test_consecutive_punctuation_does_not_create_empty_segments() -> None:
    text = "Really?! “Yes.”"
    segments = segmented(
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
    assert segmented("，。!? --", []) == []


def test_source_character_owners_locate_repeated_text_in_order() -> None:
    alignment = AlignedTranscript(
        "echo, echo echo",
        (
            AlignmentItem("echo", 0.0, 0.5),
            AlignmentItem("echo", 0.5, 1.0),
            AlignmentItem("echo", 1.0, 1.5),
        ),
    )

    assert source_character_owners(alignment) == (
        0,
        0,
        0,
        0,
        None,
        None,
        1,
        1,
        1,
        1,
        None,
        2,
        2,
        2,
        2,
    )


def test_acceptance_drops_only_quantized_zero_item_and_its_repeated_text() -> None:
    candidate = AlignedTranscript(
        "echo, echo echo",
        (
            AlignmentItem("echo", 0.0, 0.4996, 0.9),
            AlignmentItem("echo", 0.5001, 0.5004, 0.8),
            AlignmentItem("echo", 0.5004, 1.0, 0.7),
        ),
    )

    accepted, report = accept_provider_transcript(
        candidate,
        duration=1.0,
        chunk_index=3,
        language="en",
    )

    assert accepted == AlignedTranscript(
        "echo,  echo",
        (
            AlignmentItem("echo", 0.0, 0.5, 0.9),
            AlignmentItem("echo", 0.5, 1.0, 0.7),
        ),
    )
    assert report.dropped_zero_duration_items == 1
    assert report.first_start == 0.5
    assert report.last_end == 0.5


def test_acceptance_normalizes_punctuation_only_remainder_to_empty_chunk() -> None:
    accepted, report = accept_provider_transcript(
        AlignedTranscript(" word!? ", (AlignmentItem("word", 0.1, 0.1004),)),
        duration=1.0,
        chunk_index=0,
        language="en",
    )

    assert accepted == AlignedTranscript("", ())
    assert report.dropped_zero_duration_items == 1


def test_acceptance_aggregates_many_zero_duration_items_without_text_in_report() -> (
    None
):
    zero_times = [155.740 + index * (0.160 / 31) for index in range(32)]
    accepted, report = accept_provider_transcript(
        AlignedTranscript(
            "嗯" * 32 + "保留",
            (
                *(
                    AlignmentItem("嗯", timestamp, timestamp, 0.5)
                    for timestamp in zero_times
                ),
                AlignmentItem("保留", 155.900, 156.0, 0.9),
            ),
        ),
        duration=200.0,
        chunk_index=3,
        language="zh",
    )

    assert accepted.text == "保留"
    assert report == CleanupReport(
        dropped_zero_duration_items=32,
        first_start=155.740,
        last_end=155.900,
    )
    assert "嗯" not in repr(report)


@pytest.mark.parametrize(
    "item",
    [
        AlignmentItem("word", -0.0001, 0.1),
        AlignmentItem("word", 0.2, 0.1),
        AlignmentItem("word", 0.0, float("inf")),
        AlignmentItem("word", 0.0, 1.1),
        AlignmentItem("word", 0.0, 0.5, float("nan")),
    ],
)
def test_acceptance_rejects_nonrecoverable_timestamp_damage(
    item: AlignmentItem,
) -> None:
    with pytest.raises(AlignmentContractError):
        accept_provider_transcript(
            AlignedTranscript("word", (item,)),
            duration=1.0,
            chunk_index=0,
            language="en",
        )
