from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import (
    bind_transcription,
    generate_subtitle,
    open_subtitle_job,
    subtitle_job,
    transcript,
)
from scripts.subtitle_job import (
    DEFAULT_SUBTITLE_OPTIONS,
    SubtitleJobError,
    compare_normalized_correction,
    rendered_subtitle_segments,
)
from scripts.transcription_input import TranscriptionInput


def payload(text="甲乙丙", times=None, *, start="0", end="3", duration="4"):
    if times is None:
        times = [("甲", "0", "0.8"), ("乙", "1", "1.8"), ("丙", "2", "2.8")]
    return {
        "source": "audio_transcribe",
        "provider": "fake",
        "language": "zh",
        "duration": Decimal(duration),
        "segments": [{"id": 0, "start": Decimal(start), "end": Decimal(end), "text": text}],
        "items": [
            {"text": text, "start": Decimal(first), "end": Decimal(last), "probability": None}
            for text, first, last in times
        ],
    }


def render(value, **overrides):
    return rendered_subtitle_segments(value, options={**DEFAULT_SUBTITLE_OPTIONS, **overrides})


@pytest.mark.parametrize(
    "options,expected",
    [
        ({"target_chars": 2}, [(0, 1800, "甲乙"), (2000, 3000, "丙")]),
        (
            {"target_chars": 99, "target_seconds": Decimal("1.8")},
            [(0, 1800, "甲乙"), (2000, 3000, "丙")],
        ),
        ({"target_chars": 1}, [(0, 800, "甲"), (1000, 1800, "乙"), (2000, 3000, "丙")]),
        ({"min_gap": Decimal("0.20001"), "target_chars": 1}, [(0, 3000, "甲乙丙")]),
    ],
)
def test_soft_targets_exact_gap_continuous_splits_and_short_tail(options, expected):
    assert render(payload(), **options) == expected


def test_waits_for_first_qualified_gap_after_target_and_never_splits_item():
    value = payload(
        "abcdefghij kl mn",
        [("abcdefghij", "0", "1"), ("kl", "1.1", "2"), ("mn", "2.2", "3")],
    )
    assert render(value, target_chars=2) == [(0, 2000, "abcdefghij kl"), (2200, 3000, "mn")]
    value = payload("verylongword", [("verylongword", "0", "3")])
    assert render(value, target_chars=1) == [(0, 3000, "verylongword")]


def test_english_multichar_items_and_punctuation_belong_to_previous_block():
    value = payload(
        " Hello,   world! Again. ",
        [(" Hello", "0.1", "0.8"), ("world!", "1", "1.8"), ("Again", "2", "2.8")],
    )
    assert render(value, target_chars=6) == [
        (0, 800, "Hello,"),
        (1000, 1800, "world!"),
        (2000, 3000, "Again."),
    ]
    assert render(payload("甲，乙！丙。"), target_chars=2) == [
        (0, 800, "甲，"),
        (1000, 1800, "乙！"),
        (2000, 3000, "丙。"),
    ]


def test_counts_unicode_codepoints_and_punctuation_but_not_spaces():
    value = payload("😀！ a b", [("😀", "0", "0.8"), ("a", "1", "1.8"), ("b", "2", "2.8")])
    assert render(value, target_chars=2)[0] == (0, 800, "😀！")


def test_default_targets_are_24_characters_or_6_seconds():
    value = payload("甲" * 24 + "乙", [("甲" * 24, "0", "1"), ("乙", "1.2", "2")], end="2")
    assert render(value) == [(0, 1000, "甲" * 24), (1200, 2000, "乙")]
    value = payload("甲乙", [("甲", "0", "6"), ("乙", "6.2", "7")], end="7", duration="8")
    assert render(value) == [(0, 6000, "甲"), (6200, 7000, "乙")]


def test_correction_uses_existing_whitespace_normalization_before_comparing_positions():
    baseline = payload("甲， 乙！ 丙。")
    normalized = copy.deepcopy(baseline)
    normalized["segments"][0]["text"] = " 假，\n乙！\t丙。  "
    assert rendered_subtitle_segments(
        normalized, baseline, {**DEFAULT_SUBTITLE_OPTIONS, "target_chars": 2}
    ) == [(0, 800, "假，"), (1000, 1800, "乙！"), (2000, 3000, "丙。")]


@pytest.mark.parametrize(
    "corrected,split",
    [
        ("  假，乙！丙。  ", True),
        ("甲，乙！丙。丁", False),
        ("甲，丙。", False),
        ("甲！乙！丙。", False),
        ("甲， 乙！丙。", False),
    ],
)
def test_correction_preserves_offsets_only_for_equal_length_and_fixed_separators(corrected, split):
    baseline = payload("甲，乙！丙。")
    normalized = copy.deepcopy(baseline)
    normalized["segments"][0]["text"] = corrected
    before = copy.deepcopy(baseline)
    blocks = rendered_subtitle_segments(
        normalized, baseline, {**DEFAULT_SUBTITLE_OPTIONS, "target_chars": 2}
    )
    assert len(blocks) == (3 if split else 1)
    assert "".join(block[2] for block in blocks).replace(" ", "") == "".join(corrected.split())
    assert baseline == before
    assert compare_normalized_correction(baseline, normalized) == [0]


@pytest.mark.parametrize("kind", ["text", "crossing", "punctuation_only", "unowned"])
def test_legal_but_unmatched_items_fall_back_with_segment_id(kind, caplog):
    value = payload()
    if kind == "unowned":
        value["items"] = value["items"][1:]
    elif kind == "text":
        value["items"][1]["text"] = "丁"
    elif kind == "crossing":
        value["items"][0]["start"] = Decimal(0)
        value["segments"][0]["start"] = Decimal("0.1")
    else:
        value["items"][1]["text"] = "，"
    assert len(rendered_subtitle_segments(value, options=DEFAULT_SUBTITLE_OPTIONS, warn=True)) == 1
    assert "segment IDs [0]" in caplog.text


def test_keeps_segment_boundaries_and_does_not_apply_an_unowned_item():
    value = payload()
    value["segments"] = [
        {"id": 0, "start": Decimal(0), "end": Decimal("0.8"), "text": "甲"},
        {"id": 1, "start": Decimal(1), "end": Decimal(3), "text": "乙丙"},
    ]
    assert render(value, target_chars=99) == [(0, 800, "甲"), (1000, 3000, "乙丙")]


def test_preserves_transcription_timestamps_with_half_up_rounding():
    value = payload(
        "甲乙",
        [("甲", "0.0004", "1"), ("乙", "1.05", "2")],
        start="0.0004",
        end="2.0005",
        duration="2.0505",
    )
    assert render(value, target_chars=1, min_gap=Decimal("0.05")) == [
        (0, 1000, "甲"),
        (1050, 2001, "乙"),
    ]


@pytest.mark.parametrize(
    "times,expected",
    [
        (
            [("甲", "0", "0.0001"), ("乙", "0.2", "1"), ("丙", "1.2", "2")],
            [(0, 1000, "甲乙"), (1200, 2000, "丙")],
        ),
        (
            [("甲", "0", "1"), ("乙", "1.2", "1.2001"), ("丙", "1.4", "2")],
            [(0, 1200, "甲乙"), (1400, 2000, "丙")],
        ),
        (
            [("甲", "0", "1"), ("乙", "1.2", "2"), ("丙", "2.2", "2.2001")],
            [(0, 1000, "甲"), (1200, 2200, "乙丙")],
        ),
    ],
)
def test_zero_duration_split_merges_back_with_neighbor(times, expected):
    end = times[-1][2]
    value = payload(times=times, end=end)
    assert render(value, target_chars=1, min_gap=Decimal("0.19")) == expected


def test_original_segment_with_zero_duration_after_rounding_is_still_rejected():
    value = payload("甲", [("甲", "0", "0.0001")], end="0.0001")
    with pytest.raises(SubtitleJobError, match="millisecond rounding"):
        render(value)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("start", True),
        ("start", Decimal("NaN")),
        ("start", Decimal(-1)),
        ("end", 0),
        ("end", 5),
        ("probability", Decimal("1.1")),
        ("probability", False),
        ("text", ""),
        ("unexpected", 1),
    ],
)
def test_damaged_items_are_rejected(field, bad):
    value = payload()
    value["items"][0][field] = bad
    with pytest.raises(SubtitleJobError, match="items"):
        compare_normalized_correction(value, value)


def test_overlapping_empty_or_modified_items_are_rejected():
    value = payload()
    value["items"][1]["start"] = Decimal("0.7")
    with pytest.raises(SubtitleJobError, match="overlapping"):
        compare_normalized_correction(value, value)
    value["items"] = []
    with pytest.raises(SubtitleJobError, match="non-empty"):
        compare_normalized_correction(value, value)
    baseline = payload()
    normalized = copy.deepcopy(baseline)
    normalized["items"][0]["text"] = "假"
    with pytest.raises(SubtitleJobError, match="metadata"):
        compare_normalized_correction(baseline, normalized)


@pytest.fixture
def task(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    job_path = open_subtitle_job.open_subtitle_job(str(audio))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("opaque")
    value = json.loads(subtitle_job.transcript_json(payload()))
    imported = TranscriptionInput(
        audio_id=subtitle_job.sha256_file(audio),
        provider="fake",
        language="zh",
        duration=value["duration"],
        segments=value["segments"],
        items=value["items"],
    )
    monkeypatch.setattr(bind_transcription, "load_transcription", lambda _: imported)
    normalized_path = bind_transcription.bind_transcription(job_path, manifest)
    return job_path, normalized_path, audio, manifest


def test_generation_resumes_options_and_corrections_without_upstream(task, capsys):
    job_path, normalized_path, audio, manifest = task
    baseline_path = Path(subtitle_job.load_job(job_path)["artifacts"]["before_correction"])
    baseline_bytes = baseline_path.read_bytes()
    transcript.operate("edit", job_path, 0, text="假乙丙")
    manifest.unlink()
    custom = ["--target-chars", "1", "--target-seconds", "9", "--min-gap", "0.19"]
    assert generate_subtitle.main([str(job_path), *custom]) == 0
    assert capsys.readouterr().err == ""
    first = (job_path.parent / "subtitle.srt").read_bytes()
    assert first.count(b" --> ") == 3
    assert "假" in first.decode("utf-8-sig")
    assert open_subtitle_job.main([str(audio)]) == 0
    assert json.loads(capsys.readouterr().out)["subtitle"] is not None
    audio.unlink()
    assert generate_subtitle.main([str(job_path)]) == 0
    capsys.readouterr()
    assert (job_path.parent / "subtitle.srt").read_bytes() == first
    options = subtitle_job.load_job(job_path)["subtitle_options"]
    assert options == {
        **DEFAULT_SUBTITLE_OPTIONS,
        "target_chars": 1,
        "target_seconds": Decimal(9),
        "min_gap": Decimal("0.19"),
    }
    assert generate_subtitle.main([str(job_path), "--target-chars", "2"]) == 0
    capsys.readouterr()
    assert subtitle_job.load_job(job_path)["subtitle_options"] == {**options, "target_chars": 2}
    assert baseline_path.read_bytes() == baseline_bytes
    assert subtitle_job.load_job(job_path)["changed_segment_ids"] == [0]
    assert normalized_path != Path(
        subtitle_job.load_job(job_path)["artifacts"]["normalized_transcript"]
    )
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))


def test_validity_checks_use_saved_options_and_current_corrections(task, capsys):
    job_path, _, audio, _ = task
    subtitle = generate_subtitle.generate_subtitle(job_path, target_chars=1)
    job = subtitle_job.load_job(job_path)
    job["subtitle_options"]["target_chars"] = 99
    subtitle_job.atomic_write_json(job_path, job)
    with pytest.raises(SubtitleJobError, match="timeline or text"):
        subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))
    assert open_subtitle_job.main([str(audio)]) == 0
    assert json.loads(capsys.readouterr().out)["subtitle"] is None
    generate_subtitle.generate_subtitle(job_path)
    assert subtitle.read_bytes().count(b" --> ") == 1
    current = Path(subtitle_job.load_job(job_path)["artifacts"]["normalized_transcript"])
    value = subtitle_job.read_json_object(current, decimal_numbers=True)
    value["segments"][0]["text"] = "不同文字"
    subtitle_job.atomic_write_json(current, value)
    assert open_subtitle_job.main([str(audio)]) == 0
    assert json.loads(capsys.readouterr().out)["subtitle"] is None


@pytest.mark.parametrize("flag", ["--target-chars", "--target-seconds", "--min-gap"])
@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "abc"])
def test_invalid_cli_options_do_not_publish(task, flag, value, capsys):
    job_path = task[0]
    before = job_path.read_bytes()
    assert generate_subtitle.main([str(job_path), flag, value]) == 1
    assert capsys.readouterr().err
    assert job_path.read_bytes() == before
    assert not (job_path.parent / "subtitle.srt").exists()


def test_invalid_saved_options_are_rejected(task):
    job_path = task[0]
    generate_subtitle.generate_subtitle(job_path)
    job = subtitle_job.load_job(job_path)
    job["subtitle_options"]["tail_factor"] = Decimal("1.2")
    subtitle_job.atomic_write_json(job_path, job)
    with pytest.raises(SubtitleJobError, match="tail_factor"):
        generate_subtitle.generate_subtitle(job_path)


@pytest.mark.parametrize("damage", ["old_version", "missing_items", "unknown_field"])
def test_rejects_old_or_incomplete_transcript(damage):
    value = payload()
    if damage == "old_version":
        value["schema_version"] = 3
    elif damage == "missing_items":
        del value["items"]
    elif damage == "unknown_field":
        value["extra"] = True
    with pytest.raises(SubtitleJobError, match="invalid fields"):
        rendered_subtitle_segments(value)


def test_default_rendering_splits_at_item_timestamps():
    value = payload("甲" * 24 + "乙", [("甲" * 24, "0", "1"), ("乙", "1.2", "2")], end="2")
    assert rendered_subtitle_segments(value) == [(0, 1000, "甲" * 24), (1200, 2000, "乙")]


def test_recorded_subtitle_requires_saved_options(task):
    job_path = task[0]
    generate_subtitle.generate_subtitle(job_path)
    job = subtitle_job.load_job(job_path)
    job["subtitle_options"] = None
    subtitle_job.atomic_write_json(job_path, job)
    before = job_path.read_bytes()
    with pytest.raises(SubtitleJobError, match="requires subtitle_options"):
        generate_subtitle.generate_subtitle(job_path)
    assert job_path.read_bytes() == before


def test_precise_options_survive_reload_and_text_reset(task, capsys):
    job_path = task[0]
    precise_gap = Decimal("0.20000000000000000000000000001")
    subtitle = generate_subtitle.generate_subtitle(job_path, target_chars=1, min_gap=precise_gap)
    assert subtitle.read_bytes().count(b" --> ") == 1
    transcript.operate("edit", job_path, 0, text="假乙丙")
    transcript.operate("reset", job_path, 0)
    assert subtitle_job.load_job(job_path)["subtitle_options"]["min_gap"] == precise_gap
    assert generate_subtitle.main([str(job_path)]) == 0
    capsys.readouterr()
    assert subtitle.read_bytes().count(b" --> ") == 1
    assert subtitle_job.load_job(job_path)["subtitle_options"]["min_gap"] == precise_gap


def test_noninteger_target_chars_is_rejected(task, capsys):
    job_path = task[0]
    before = job_path.read_bytes()
    assert generate_subtitle.main([str(job_path), "--target-chars", "1.5"]) == 1
    assert "positive integer" in capsys.readouterr().err
    assert job_path.read_bytes() == before


def test_json_generation_restores_corrected_job_without_srt(task, capsys):
    job_path, _, audio, _ = task
    transcript.operate("edit", job_path, 0, text="假乙丙")
    assert generate_subtitle.main([str(job_path), "--format", "json", "--target-chars", "1"]) == 0
    capsys.readouterr()
    subtitle = job_path.parent / "subtitle.json"
    assert not (job_path.parent / "subtitle.srt").exists()
    assert json.loads(subtitle.read_text(encoding="utf-8")) == {
        "version": 1,
        "cues": [
            {"id": "1", "text": "假", "startMs": 0, "endMs": 800},
            {"id": "2", "text": "乙", "startMs": 1000, "endMs": 1800},
            {"id": "3", "text": "丙", "startMs": 2000, "endMs": 3000},
        ],
    }
    assert open_subtitle_job.main([str(audio)]) == 0
    assert json.loads(capsys.readouterr().out)["subtitle"] == str(subtitle)
    subtitle.write_text("damaged", encoding="utf-8")
    assert open_subtitle_job.main([str(audio)]) == 0
    assert json.loads(capsys.readouterr().out)["subtitle"] is None
    restored = generate_subtitle.generate_subtitle(job_path)
    assert restored == subtitle
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))
    srt = generate_subtitle.generate_subtitle(job_path, output_format="srt")
    assert srt.suffix == ".srt"
    assert "00:00:00,000 --> 00:00:00,800" in srt.read_text(encoding="utf-8-sig")


def test_legacy_reading_tail_options_keep_corrections_and_regenerate(task, capsys):
    job_path = task[0]
    transcript.operate("edit", job_path, 0, text="假乙丙")
    generate_subtitle.generate_subtitle(job_path, target_chars=1)
    job = subtitle_job.load_job(job_path)
    baseline = Path(job["artifacts"]["before_correction"]).read_bytes()
    job["subtitle_options"]["tail_factor"] = Decimal("1.1")
    subtitle_job.atomic_write_json(job_path, job)
    old_subtitle = Path(job["artifacts"]["subtitle"])
    old_subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,880\n假\n\n"
        "2\n00:00:01,000 --> 00:00:01,880\n乙\n\n"
        "3\n00:00:02,000 --> 00:00:03,100\n丙\n",
        encoding="utf-8-sig",
    )
    original_job = job_path.read_bytes()
    assert open_subtitle_job.main([str(task[2])]) == 0
    assert json.loads(capsys.readouterr().out)["subtitle"] is None
    assert job_path.read_bytes() == original_job
    subtitle = generate_subtitle.generate_subtitle(job_path, output_format="json")
    updated = subtitle_job.load_job(job_path)
    assert "tail_factor" not in updated["subtitle_options"]
    assert updated["changed_segment_ids"] == [0]
    assert Path(updated["artifacts"]["before_correction"]).read_bytes() == baseline
    assert json.loads(subtitle.read_text(encoding="utf-8"))["cues"][0]["endMs"] == 800
