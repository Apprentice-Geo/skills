import argparse
from pathlib import Path

import transcribe
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
        batch_size=8,
        beam_size=5,
        cpu_threads=0,
        num_workers=1,
        word_timestamps=False,
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
