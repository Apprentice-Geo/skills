import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import bind_transcription, generate_srt, open_subtitle_job, subtitle_job, transcript
from scripts.transcription_input import TranscriptionInput


@pytest.fixture
def task(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    job_path = open_subtitle_job.open_subtitle_job(str(audio))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("opaque")
    snapshot = TranscriptionInput(
        audio_id=subtitle_job.sha256_file(audio),
        provider="fake",
        language="zh",
        duration=6,
        segments=[{"id": i, "start": i, "end": i + 1, "text": f"原文{i}"} for i in range(5)],
    )
    monkeypatch.setattr(bind_transcription, "load_transcription", lambda _: snapshot)
    path = bind_transcription.bind_transcription(job_path, manifest)
    # 导入后构建精细小数的可信本地基准，不经过 float。
    value = subtitle_job.read_json_object(path, decimal_numbers=True)
    value["duration"] = Decimal("6.12345678901234567890123456789")
    value["segments"][0]["start"] = Decimal("0.00012345678901234567890123456789")
    value["segments"][0]["end"] = Decimal("0.99912345678901234567890123456789")
    content = (subtitle_job.transcript_json(value) + "\n").encode("utf-8")
    subtitle_job.publish_transcript(job_path, subtitle_job.load_job(job_path), content)
    return job_path, audio, manifest, value, content


@pytest.mark.parametrize(
    "target,context,ids",
    [
        (2, None, [2]),
        (2, 1, [1, 2, 3]),
        (0, 2, [0, 1, 2]),
        (4, 2, [2, 3, 4]),
        ("all", None, [0, 1, 2, 3, 4]),
        (2, 20, [0, 1, 2, 3, 4]),
    ],
)
def test_show_preserves_artifacts_and_precision(task, target, context, ids):
    job_path, audio, manifest, value, _ = task
    audio.unlink()
    manifest.unlink()
    job = subtitle_job.load_job(job_path)
    files = [
        job_path,
        Path(job["artifacts"]["normalized_transcript"]),
        Path(job["artifacts"]["before_correction"]),
    ]
    before = {p: p.read_bytes() for p in files}
    output = transcript.operate("show", job_path, target, context=context)
    assert output["target_id"] == (None if target == "all" else target)
    assert [s["id"] for s in output["segments"]] == ids
    assert output["segments"] == [value["segments"][i] for i in ids]
    encoded = subtitle_job.transcript_json(output)
    assert json.loads(encoded, parse_float=Decimal) == output
    assert all(p.read_bytes() == content for p, content in before.items())


def test_edit_reset_records_precision_and_manual_subtitle(task, capsys):
    job_path, _, _, original, baseline = task
    old_srt = generate_srt.generate_srt(job_path)
    old_content = old_srt.read_bytes()
    text = '  中文 "引号"\n下一行  '
    first = transcript.operate("edit", job_path, 0, text=text)
    second = transcript.operate("edit", job_path, 2, text="校正二")
    assert second != first
    current = subtitle_job.read_json_object(second, decimal_numbers=True)
    assert current["segments"][0]["text"] == text
    assert current["duration"] == original["duration"]
    assert current["segments"][0]["start"] == original["segments"][0]["start"]
    assert current["segments"][0]["end"] == original["segments"][0]["end"]
    assert current["segments"][1] == original["segments"][1]
    job = subtitle_job.load_job(job_path)
    assert job["changed_segment_ids"] == [0, 2]
    assert job["artifacts"]["subtitle"] is None
    assert Path(job["artifacts"]["before_correction"]).read_bytes() == baseline
    subtitle_job.validate_job(job_path, job)
    assert open_subtitle_job.main([str(task[1])]) == 0
    assert json.loads(capsys.readouterr().out)["subtitle"] is None
    assert old_srt.read_bytes() == old_content
    reset = transcript.operate("reset", job_path, 0)
    assert (
        subtitle_job.read_json_object(reset, decimal_numbers=True)["segments"][0]
        == original["segments"][0]
    )
    assert subtitle_job.load_job(job_path)["changed_segment_ids"] == [2]
    repeated = transcript.operate("reset", job_path, 0)
    assert repeated != reset
    generated = generate_srt.generate_srt(job_path)
    assert "校正二" in generated.read_text(encoding="utf-8-sig")
    assert transcript.operate("reset", job_path, "all").read_bytes() == baseline


@pytest.mark.parametrize(
    "command,id,text,context",
    [
        ("show", -1, None, None),
        ("show", 5, None, None),
        ("edit", "all", "x", None),
        ("edit", 0, "", None),
        ("edit", 0, " \n\t", None),
        ("show", "all", None, 0),
        ("show", 0, None, -1),
        ("reset", 5, None, None),
        ("edit", 5, "x", None),
    ],
)
def test_invalid_operation_preserves_job(task, command, id, text, context):
    job_path = task[0]
    before = job_path.read_bytes()
    with pytest.raises(subtitle_job.SubtitleJobError):
        transcript.operate(command, job_path, id, text=text, context=context)
    assert job_path.read_bytes() == before


@pytest.mark.parametrize(
    "arguments",
    [
        ["show", "--id", "-1"],
        ["show", "--id", "1.0"],
        ["show", "--id", "1-3"],
        ["show", "--id", "all", "--context", "0"],
        ["show", "--id", "0", "--context", "all"],
        ["show", "--id", "0", "--context", "-1"],
        ["edit", "--id", "all", "--text", "x"],
        ["edit", "--id", "0", "--text", " \n"],
        ["reset", "--id", "ALL"],
    ],
)
def test_invalid_cli_arguments_are_not_logged(task, arguments, capsys):
    command, *rest = arguments
    assert transcript.main([command, str(task[0]), *rest]) == 1
    terminal = capsys.readouterr()
    assert terminal.out == "" and terminal.err.startswith("error:")
    assert not list(task[0].parent.glob("transcript-*.log"))


@pytest.mark.parametrize("command", ["show", "edit", "reset"])
@pytest.mark.parametrize("damage", ["baseline", "json", "timeline", "missing"])
def test_invalid_artifacts_refused(task, command, damage):
    job_path = task[0]
    job = subtitle_job.load_job(job_path)
    path = Path(job["artifacts"]["normalized_transcript"])
    if damage == "baseline":
        Path(job["artifacts"]["before_correction"]).write_bytes(b"bad")
    elif damage == "missing":
        path.unlink()
    elif damage == "json":
        path.write_bytes(b"bad")
    else:
        value = subtitle_job.read_json_object(path, decimal_numbers=True)
        value["segments"][0]["end"] = 2
        path.write_text(subtitle_job.transcript_json(value), encoding="utf-8")
    before = job_path.read_bytes()
    with pytest.raises(subtitle_job.SubtitleJobError):
        transcript.operate(command, job_path, 0, text="replacement")
    assert job_path.read_bytes() == before


@pytest.mark.parametrize("command", ["edit", "reset"])
@pytest.mark.parametrize("failure", ["write", "commit"])
def test_publish_failure_keeps_existing_results(task, monkeypatch, command, failure):
    job_path = task[0]
    srt = generate_srt.generate_srt(job_path)
    normalized = Path(subtitle_job.load_job(job_path)["artifacts"]["normalized_transcript"])
    before = {p: p.read_bytes() for p in (job_path, normalized, srt)}
    original_open = Path.open

    def fail(*args, **kwargs):
        raise OSError("injected publication failure")

    def fail_write(path, mode="r", *args, **kwargs):
        if mode == "wb":
            fail()
        return original_open(path, mode, *args, **kwargs)

    if failure == "commit":
        monkeypatch.setattr(subtitle_job, "atomic_write_json", fail)
    else:
        monkeypatch.setattr(Path, "open", fail_write)
    with pytest.raises(OSError, match="publication failure"):
        transcript.operate(command, job_path, 0, text="changed")
    assert all(p.read_bytes() == content for p, content in before.items())


def test_cli_external_data_logs_no_text_and_legacy_job(task, monkeypatch, capsys):
    job_path = task[0]
    data = job_path.parents[2]
    monkeypatch.setenv("SUBTITLE_CREATOR_DATA_DIR", str(data / "other"))
    common = [str(job_path), "--id", "0", "--data-dir", str(data)]
    before = job_path.read_bytes()
    assert transcript.main(["show", *common, "--context", "1"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["target_id"] == 0
    assert job_path.read_bytes() == before
    secret = "私密编辑正文"
    assert transcript.main(["edit", *common, "--text", secret]) == 0
    assert capsys.readouterr().out.startswith("normalized_transcript: ")
    job = subtitle_job.load_job(job_path)
    job["schema_version"] = 2
    job["status"] = "editable"
    subtitle_job.atomic_write_json(job_path, job)
    assert transcript.main(["reset", *common]) == 0
    capsys.readouterr()
    assert subtitle_job.load_job(job_path)["schema_version"] == 3
    for log in job_path.parent.glob("transcript-*.log"):
        text = log.read_text(encoding="utf-8")
        assert secret not in text and "原文" not in text
        assert "segments" in text


def test_strict_correction_records_without_subtitle(task):
    job_path = task[0]
    transcript.operate("edit", job_path, 1, text="changed")
    job = subtitle_job.load_job(job_path)
    job["changed_segment_ids"] = []
    with pytest.raises(subtitle_job.SubtitleJobError, match="changed_segment_ids"):
        subtitle_job.validate_job(job_path, job)


@pytest.mark.parametrize("damage", ["missing", "stale"])
def test_segment_operations_allow_unavailable_subtitle(task, damage):
    job_path, audio, manifest, _, _ = task
    subtitle = generate_srt.generate_srt(job_path)
    if damage == "missing":
        subtitle.unlink()
    else:
        subtitle.write_bytes(b"stale")
    audio.unlink()
    manifest.unlink()
    assert transcript.operate("show", job_path, 0)["target_id"] == 0
    transcript.operate("edit", job_path, 0, text="校正")
    transcript.operate("reset", job_path, 0)
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))


def test_unchanged_edit_still_publishes_and_clears_subtitle(task):
    job_path = task[0]
    generate_srt.generate_srt(job_path)
    before = subtitle_job.load_job(job_path)
    path = transcript.operate("edit", job_path, 0, text=task[3]["segments"][0]["text"])
    assert str(path) != before["artifacts"]["normalized_transcript"]
    after = subtitle_job.load_job(job_path)
    assert after["changed_segment_ids"] == []
    assert after["artifacts"]["subtitle"] is None
