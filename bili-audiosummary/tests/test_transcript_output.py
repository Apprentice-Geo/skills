from copy import deepcopy

from scripts import transcript_output
from scripts.utils import read_json, write_json


def test_normalize_segments_for_markdown_uses_punctuation_and_duration() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "第一段"},
        {"id": 1, "start": 2.0, "end": 3.0, "text": "继续，"},
        {"id": 2, "start": 3.2, "end": 3.8, "text": "短句。"},
        {"id": 3, "start": 4.0, "end": 8.0, "text": "没有标点"},
        {"id": 4, "start": 8.0, "end": 14.1, "text": "继续内容"},
    ]
    original = deepcopy(segments)

    normalized = transcript_output.normalize_segments_for_markdown(segments, "zh")

    assert normalized == [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "第一段，继续，"},
        {"id": 1, "start": 3.2, "end": 3.8, "text": "短句。"},
        {"id": 2, "start": 4.0, "end": 14.1, "text": "没有标点，继续内容"},
    ]
    assert segments == original


def test_normalize_segments_for_markdown_uses_character_limit() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "甲" * 25},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "乙" * 25},
        {"id": 2, "start": 2.0, "end": 3.0, "text": "丙" * 10},
    ]

    normalized = transcript_output.normalize_segments_for_markdown(segments, "zh")

    assert normalized == [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "甲" * 25 + "，" + "乙" * 25},
        {"id": 1, "start": 2.0, "end": 3.0, "text": "丙" * 10},
    ]


def test_normalize_segments_for_markdown_breaks_at_silence_gap() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "前文"},
        {"id": 1, "start": 2.5, "end": 3.0, "text": "后文"},
    ]

    normalized = transcript_output.normalize_segments_for_markdown(segments, "zh")

    assert [segment["text"] for segment in normalized] == ["前文", "后文"]


def test_normalize_segments_for_markdown_joins_english_with_commas() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "first"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "second"},
    ]

    normalized = transcript_output.normalize_segments_for_markdown(segments, "en")

    assert normalized[0]["text"] == "first, second"


def test_write_markdown_from_json_preserves_json_and_compacts_transcript_lines(
    workspace_tmp_path,
) -> None:
    json_path = workspace_tmp_path / "transcript.json"
    markdown_path = workspace_tmp_path / "transcript.md"
    payload = {
        "source": "faster-whisper",
        "language": "zh",
        "segments": [
            {"id": 0, "start": 0.0, "end": 4.0, "text": "第一段"},
            {"id": 1, "start": 4.0, "end": 10.0, "text": "第二段"},
            {"id": 2, "start": 10.0, "end": 11.0, "text": "结束。"},
        ],
    }
    write_json(json_path, payload)

    transcript_output.write_markdown_from_json(
        json_path,
        markdown_path,
        normalize_segments=True,
    )

    assert read_json(json_path) == payload
    markdown = markdown_path.read_text(encoding="utf-8")
    assert (
        "[00:00:00 - 00:00:10] 第一段，第二段\n[00:00:10 - 00:00:11] 结束。"
    ) in markdown
