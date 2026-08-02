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


def test_subtitle_to_transcript_skips_zero_duration_cue_and_warns(
    workspace_tmp_path: Path,
    manifest_payload: dict,
    metadata_payload: dict,
    capsys,
) -> None:
    subtitle_path = workspace_tmp_path / "mixed.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:01,000\nskip me\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nkeep me\n",
        encoding="utf-8",
    )
    log_path = workspace_tmp_path / "subtitle.log"

    with LoggingSession(log_path):
        result = subtitle_transcript.subtitle_to_transcript(
            subtitle_path=subtitle_path,
            manifest=manifest_payload,
            metadata=metadata_payload,
            output_dir=workspace_tmp_path,
        )

    warning = (
        f"Skipping zero-duration subtitle cue: {subtitle_path.as_posix()} "
        "(cue 1, 00:00:01,000 --> 00:00:01,000)"
    )
    assert result["segments"] == [
        {"id": 0, "start": 2.0, "end": 3.0, "text": "keep me"}
    ]
    assert warning in capsys.readouterr().out
    assert warning in log_path.read_text(encoding="utf-8")


def test_probe_srt_rejects_file_with_only_zero_duration_cues(
    workspace_tmp_path: Path,
) -> None:
    subtitle_path = workspace_tmp_path / "zero.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:01,000\nskip me\n",
        encoding="utf-8",
    )

    segments, error = subtitle_transcript.probe_srt(subtitle_path)

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
    assert result["markdown_path"] == workspace_tmp_path / "transcript.md"
    assert "title: 测试视频" in markdown
    assert "bvid: BVTEST" in markdown
    assert "url: https://www.bilibili.com/video/BVTEST/" in markdown
    assert "uploader: 测试作者" in markdown
    assert "duration: 00:07" in markdown
    assert "source: subtitle" in markdown
    assert "[00:00:01 - 00:00:03] 第一句话。" in markdown
    assert (
        "[00:00:01 - 00:00:03] 第一句话。\n[00:00:04 - 00:00:07] 第二句话 换行继续。"
    ) in markdown


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
