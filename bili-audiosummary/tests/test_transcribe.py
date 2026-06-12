import argparse
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import transcribe
from process_logging import LoggingSession
from runtime_options import TranscribeOptions
from utils import write_json


def make_args(manifest_path: Path, output_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        input=None,
        manifest=manifest_path,
        audio=None,
        output_dir=output_dir,
        asr_provider="whisper",
        model=None,
        language="zh",
        device="cpu",
        compute_type="float32",
        beam_size=5,
        cpu_threads=0,
        num_workers=1,
    )


def test_resolve_inputs_uses_first_audio_from_manifest(workspace_tmp_path: Path) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    manifest_path = workspace_tmp_path / "fetch_manifest.json"
    write_json(manifest_path, {"id": "BVTEST", "audio_files": [audio_path.as_posix()]})

    resolved_manifest, resolved_audio, manifest = transcribe.resolve_inputs(
        argparse.Namespace(input=str(manifest_path), manifest=None, audio=None)
    )

    assert resolved_manifest == manifest_path
    assert resolved_audio == audio_path
    assert manifest["id"] == "BVTEST"


def test_run_transcribe_writes_outputs_with_mocked_asr(workspace_tmp_path: Path, mocker) -> None:
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    resource_dir.mkdir(parents=True)
    audio_path = resource_dir / "BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    metadata_path = resource_dir / "metadata.json"
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(metadata_path, {"uploader": "测试作者", "duration_string": "00:01"})
    write_json(
        manifest_path,
        {
            "id": "BVTEST",
            "title": "测试视频",
            "url": "https://www.bilibili.com/video/BVTEST/",
            "metadata_path": metadata_path.as_posix(),
            "audio_files": [audio_path.as_posix()],
        },
    )
    mocker.patch(
        "transcribe.transcribe_audio",
        return_value=(
            {"language": "zh", "duration": 1.0},
            [{"id": 0, "start": 0.0, "end": 1.0, "text": "测试文本"}],
            "faster-whisper",
        ),
    )

    result = transcribe.run_transcribe(make_args(manifest_path, result_dir))

    assert result["json_path"].exists()
    assert result["markdown_path"].exists()
    assert result["payload"]["source"] == "faster-whisper"
    assert result["payload"]["segments"][0]["text"] == "测试文本"


def test_transcribe_audio_returns_model_segments_with_simplified_chinese(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    audio_path = workspace_tmp_path / "audio.m4a"
    audio_path.write_bytes(b"audio")
    model_segments = [
        SimpleNamespace(id=1, start=0.0, end=4.0, text="第一段繁體。"),
        SimpleNamespace(id=2, start=4.0, end=9.0, text="第二段內容。"),
    ]

    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, *_args, **_kwargs):
            return iter(model_segments), SimpleNamespace(
                language="zh",
                language_probability=1.0,
                duration=9.0,
                duration_after_vad=9.0,
            )

    faster_whisper = types.ModuleType("faster_whisper")
    faster_whisper.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)

    _info, segments, source = transcribe.transcribe_audio(
        audio_path,
        TranscribeOptions(language="zh"),
    )

    assert source == "faster-whisper"
    assert segments == [
        {"id": 1, "start": 0.0, "end": 4.0, "text": "第一段繁体。"},
        {"id": 2, "start": 4.0, "end": 9.0, "text": "第二段内容。"},
    ]


def test_transcribe_cli_rejects_word_timestamps_option(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["transcribe.py", "audio.m4a", "--word-timestamps"],
    )

    with pytest.raises(SystemExit):
        transcribe.parse_args()


def test_run_transcribe_only_emits_stage(
    workspace_tmp_path: Path,
    capsys,
    mocker,
) -> None:
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    resource_dir.mkdir(parents=True)
    audio_path = resource_dir / "BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(
        manifest_path,
        {"id": "BVTEST", "audio_files": [audio_path.as_posix()]},
    )
    mocker.patch(
        "transcribe.transcribe_audio",
        return_value=(
            {"language": "zh"},
            [{"id": 0, "start": 0.0, "end": 1.0, "text": "测试"}],
            "faster-whisper",
        ),
    )

    with LoggingSession(workspace_tmp_path / "transcribe.log"):
        transcribe.run_transcribe(make_args(manifest_path, result_dir))

    assert capsys.readouterr().out == "[Stage] Transcribe audio with whisper\n"
