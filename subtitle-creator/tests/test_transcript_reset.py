from pathlib import Path

import pytest

from scripts import (
    bind_transcription,
    generate_srt,
    open_subtitle_job,
    subtitle_job,
    transcript,
)
from scripts.transcription_input import TranscriptionInput


@pytest.fixture
def bound_job(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    job_path = open_subtitle_job.open_subtitle_job(str(audio))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("opaque")
    transcription = TranscriptionInput(
        audio_id=subtitle_job.sha256_file(audio),
        provider="fake",
        language="zh",
        duration=2,
        segments=[{"id": 0, "start": 0, "end": 1, "text": "原始文本"}],
    )
    monkeypatch.setattr(bind_transcription, "load_transcription", lambda _: transcription)
    normalized_path = bind_transcription.bind_transcription(job_path, manifest)
    return job_path, normalized_path, audio, manifest


@pytest.mark.parametrize("damage", ["edited", "missing", "json", "timeline"])
def test_reset_restores_bound_snapshot_without_audio_or_upstream(bound_job, damage):
    job_path, normalized_path, audio, manifest = bound_job
    original_job = subtitle_job.read_json_object(job_path)
    baseline = Path(original_job["artifacts"]["before_correction"]).read_bytes()
    normalized = subtitle_job.read_json_object(normalized_path)
    normalized["segments"][0]["text"] = "校正文本"
    subtitle_job.atomic_write_json(normalized_path, normalized)
    generate_srt.generate_srt(job_path)
    if damage == "missing":
        normalized_path.unlink()
    elif damage == "json":
        normalized_path.write_bytes(b"invalid JSON")
    elif damage == "timeline":
        normalized["segments"][0]["start"] = 0.5
        subtitle_job.atomic_write_json(normalized_path, normalized)
    audio.unlink()
    manifest.unlink()

    new_path = transcript.operate("reset", job_path, "all")

    assert new_path.read_bytes() == baseline
    job = subtitle_job.read_json_object(job_path)
    assert job["status"] == "transcription_bound"
    assert job["changed_segment_ids"] == []
    assert job["artifacts"]["subtitle"] is None
    assert Path(job["artifacts"]["before_correction"]).read_bytes() == baseline
    subtitle_job.validate_job(job_path, job)
    assert generate_srt.generate_srt(job_path).is_file()
    assert transcript.operate("reset", job_path, "all").read_bytes() == baseline


def test_reset_rejects_damaged_baseline_without_publishing(bound_job):
    job_path, normalized_path, _audio, _manifest = bound_job
    before = job_path.read_bytes()
    normalized_bytes = normalized_path.read_bytes()
    job = subtitle_job.read_json_object(job_path)
    Path(job["artifacts"]["before_correction"]).write_bytes(b"damaged")
    with pytest.raises(subtitle_job.SubtitleJobError, match="digest mismatch"):
        transcript.operate("reset", job_path, "all")
    assert job_path.read_bytes() == before
    assert normalized_path.read_bytes() == normalized_bytes


def test_reset_failed_publish_preserves_corrections_and_subtitle(bound_job, monkeypatch):
    job_path, normalized_path, _audio, _manifest = bound_job
    normalized = subtitle_job.read_json_object(normalized_path)
    normalized["segments"][0]["text"] = "keep correction"
    subtitle_job.atomic_write_json(normalized_path, normalized)
    subtitle_path = generate_srt.generate_srt(job_path)
    before = {path: path.read_bytes() for path in (job_path, normalized_path, subtitle_path)}
    with monkeypatch.context() as patch:

        def fail_publish(*args, **kwargs):
            raise OSError("publish failed")

        patch.setattr(subtitle_job, "atomic_write_json", fail_publish)
        with pytest.raises(OSError, match="publish failed"):
            transcript.operate("reset", job_path, "all")
    for path, content in before.items():
        assert path.read_bytes() == content
    subtitle_job.validate_job(job_path, subtitle_job.load_job(job_path))
    assert transcript.operate("reset", job_path, "all").is_file()


def test_reset_rejects_unbound_job_and_relative_path(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    job_path = open_subtitle_job.open_subtitle_job(str(audio))
    with pytest.raises(subtitle_job.SubtitleJobError, match="no bound transcription"):
        transcript.operate("reset", job_path, "all")
    with pytest.raises(subtitle_job.SubtitleJobError, match="absolute"):
        transcript.operate("reset", Path("subtitle_job.json"), "all")


def test_reset_cli_outputs_current_path_and_moves_log(bound_job, capsys):
    job_path, _normalized_path, _audio, _manifest = bound_job
    assert transcript.main(["reset", str(job_path), "--id", "all"]) == 0
    output = capsys.readouterr()
    job = subtitle_job.read_json_object(job_path)
    assert output.out == f"normalized_transcript: {job['artifacts']['normalized_transcript']}\n"
    assert output.err == ""
    assert len(list(job_path.parent.glob("transcript-*.log"))) == 1
