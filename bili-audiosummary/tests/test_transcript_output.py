from scripts import transcript_output
from scripts.utils import read_json, write_json


def test_write_markdown_from_json_preserves_json_and_renders_one_segment_per_line(
    workspace_tmp_path,
) -> None:
    json_path = workspace_tmp_path / "transcript.json"
    markdown_path = workspace_tmp_path / "transcript.md"
    payload = {
        "source": "faster-whisper",
        "language": "zh",
        "segments": [
            {"id": 0, "start": 0.0, "end": 4.0, "text": "第一段"},
            {"id": 1, "start": 4.0, "end": 10.0, "text": ""},
            {"id": 2, "start": 10.0, "end": 11.0, "text": "结束。"},
        ],
    }
    write_json(json_path, payload)

    transcript_output.write_markdown_from_json(json_path, markdown_path)

    assert read_json(json_path) == payload
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "[00:00:00 - 00:00:04] 第一段\n[00:00:10 - 00:00:11] 结束。" in markdown
    assert "第一段，" not in markdown
    assert "第一段\n\n[" not in markdown
