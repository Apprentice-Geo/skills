from scripts import transcript_output
from scripts.utils import read_json, write_json


def test_write_markdown_from_json_preserves_json_and_merges_segments(
    workspace_tmp_path,
) -> None:
    json_path = workspace_tmp_path / "transcript.json"
    markdown_path = workspace_tmp_path / "transcript.md"
    payload = {
        "title": "Title",
        "bvid": "BVTEST",
        "url": "https://www.bilibili.com/video/BVTEST/",
        "uploader": "Uploader",
        "duration": 80.0,
        "source": "audio_transcribe",
        "language": "zh",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": " first "},
            {"id": 1, "start": 6.0, "end": 7.0, "text": "second"},
            {"id": 2, "start": 12.1, "end": 13.0, "text": "时间中断"},
            {"id": 3, "start": 13.1, "end": 14.0, "text": "，保留标点。"},
            {"id": 4, "start": 14.1, "end": 15.0, "text": "下一句！"},
            {"id": 5, "start": 15.1, "end": 16.0, "text": "English?"},
            {"id": 6, "start": 16.1, "end": 17.0, "text": "末句"},
        ],
    }
    write_json(json_path, payload)

    transcript_output.write_markdown_from_json(json_path, markdown_path)

    assert read_json(json_path) == payload
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "title: Title" in markdown
    assert "duration: 80.0" in markdown
    rows = [line for line in markdown.splitlines() if line.startswith("[")]
    assert rows == [
        "[00:00:00 - 00:00:07] first second",
        "[00:00:12 - 00:00:14] 时间中断 ，保留标点。",
        "[00:00:14 - 00:00:15] 下一句！",
        "[00:00:15 - 00:00:16] English?",
        "[00:00:16 - 00:00:17] 末句",
    ]
    assert "\n\n[" not in "\n".join(rows)


def test_merge_ends_after_exceeding_64_characters_without_splitting_segment(
    workspace_tmp_path,
) -> None:
    json_path = workspace_tmp_path / "transcript.json"
    markdown_path = workspace_tmp_path / "transcript.md"
    long_text = "x" * 65
    write_json(
        json_path,
        {
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": "a" * 32},
                {"id": 1, "start": 1.0, "end": 2.0, "text": "b" * 32},
                {"id": 2, "start": 2.0, "end": 3.0, "text": "next."},
                {"id": 3, "start": 3.0, "end": 4.0, "text": long_text},
                {"id": 4, "start": 4.0, "end": 5.0, "text": "tail"},
            ]
        },
    )

    transcript_output.write_markdown_from_json(json_path, markdown_path)

    rows = [
        line
        for line in markdown_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("[")
    ]
    assert rows == [
        f"[00:00:00 - 00:00:02] {'a' * 32} {'b' * 32}",
        "[00:00:02 - 00:00:03] next.",
        f"[00:00:03 - 00:00:04] {long_text}",
        "[00:00:04 - 00:00:05] tail",
    ]


def test_markdown_text_is_single_line_and_bounded() -> None:
    payload = {
        "title": " title\nwith\tcontrol\x00",
        "segments": [
            {"id": 0, "start": 0, "end": 1, "text": " hello\nworld\r\x1b[31m"}
        ],
    }
    markdown = transcript_output.render_markdown(payload)
    assert "title: title with control" in markdown
    assert "[00:00:00 - 00:00:01] hello world [31m" in markdown
    assert all(
        "\x00" not in line and "\n" not in line for line in markdown.splitlines()
    )
