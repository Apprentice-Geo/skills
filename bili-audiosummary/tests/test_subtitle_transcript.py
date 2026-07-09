from pathlib import Path

import scripts.subtitle_transcript as subtitle_transcript
from scripts.process_logging import LoggingSession
from scripts.utils import read_json


def test_parse_srt_timestamp() -> None:
    assert subtitle_transcript.parse_srt_timestamp("01:02:03,456") == 3723.456


def test_parse_srt_handles_bom_and_multiline_text(sample_srt_path: Path) -> None:
    segments = subtitle_transcript.parse_srt(sample_srt_path)

    assert segments == [
        {"id": 0, "start": 1.0, "end": 3.5, "text": "第一句话。"},
        {"id": 1, "start": 4.0, "end": 7.25, "text": "第二句话 换行继续。"},
    ]


def test_probe_srt_rejects_invalid_srt(invalid_srt_path: Path) -> None:
    segments, error = subtitle_transcript.probe_srt(invalid_srt_path)

    assert segments is None
    assert error == "Subtitle SRT is empty or invalid."


def test_subtitle_to_transcript_writes_json_and_markdown(
    workspace_tmp_path: Path,
    sample_srt_path: Path,
    manifest_payload: dict,
    metadata_payload: dict,
) -> None:
    result = subtitle_transcript.subtitle_to_transcript(
        subtitle_path=sample_srt_path,
        manifest=manifest_payload,
        metadata=metadata_payload,
        output_dir=workspace_tmp_path,
    )

    payload = read_json(result["json_path"])
    markdown = result["markdown_path"].read_text(encoding="utf-8")

    assert payload["bvid"] == "BVTEST"
    assert payload["source"] == "subtitle"
    assert payload["language"] == "zh-Hans"
    assert payload["segments"][0]["text"] == "第一句话。"
    assert "source: subtitle" in markdown
    assert "[00:00:01 - 00:00:03] 第一句话。" in markdown


def test_subtitle_to_transcript_only_emits_stage(
    workspace_tmp_path: Path,
    sample_srt_path: Path,
    manifest_payload: dict,
    metadata_payload: dict,
    capsys,
) -> None:
    with LoggingSession(workspace_tmp_path / "subtitle.log"):
        subtitle_transcript.subtitle_to_transcript(
            subtitle_path=sample_srt_path,
            manifest=manifest_payload,
            metadata=metadata_payload,
            output_dir=workspace_tmp_path,
        )

    assert capsys.readouterr().out == "[Stage] Build transcript from subtitle\n"
