import argparse
import sys
import types
from pathlib import Path

import pytest

import scripts.transcribe as transcribe
from scripts.process_logging import LoggingSession
from scripts.runtime_options import TranscribeOptions
from scripts.utils import read_json, write_json


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
        cpu_threads=None,
        num_workers=None,
    )


def test_resolve_inputs_uses_first_audio_from_manifest(
    workspace_tmp_path: Path,
) -> None:
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


def test_run_transcribe_writes_outputs_with_mocked_asr(
    workspace_tmp_path: Path, mocker
) -> None:
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
        "scripts.transcribe.parallel_asr.run_parallel_whisper_transcribe",
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


@pytest.mark.parametrize(
    "failure",
    [
        "Qwen3 ASR dependencies are not installed",
        "Qwen3 local models are missing",
        "Qwen3 ASR requires an available CUDA GPU",
        "Qwen3 inference failed",
    ],
)
def test_run_transcribe_propagates_qwen3_failure_without_whisper_fallback(
    workspace_tmp_path: Path,
    capsys,
    monkeypatch,
    mocker,
    failure: str,
) -> None:
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    resource_dir.mkdir(parents=True)
    audio_path = resource_dir / "BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(manifest_path, {"id": "BVTEST", "audio_files": [audio_path.as_posix()]})
    args = make_args(manifest_path, result_dir)
    args.asr_provider = "qwen3"
    faster_whisper = types.ModuleType("faster_whisper")
    faster_whisper.WhisperModel = lambda *_args, **_kwargs: pytest.fail(
        "WhisperModel loaded"
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)
    qwen3_mock = mocker.patch(
        "scripts.transcribe.transcribe_with_qwen3",
        side_effect=RuntimeError(failure),
    )
    parallel_mock = mocker.patch(
        "scripts.transcribe.parallel_asr.run_parallel_whisper_transcribe"
    )

    with pytest.raises(RuntimeError, match=failure):
        transcribe.run_transcribe(args)

    qwen3_mock.assert_called_once()
    parallel_mock.assert_not_called()
    assert "falling back" not in capsys.readouterr().out.lower()
    assert not (result_dir / "BVTEST_transcript.json").exists()
    assert not (result_dir / "BVTEST_transcript.md").exists()


def test_transcribe_cli_rejects_word_timestamps_option(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["transcribe.py", "audio.m4a", "--word-timestamps"],
    )

    with pytest.raises(SystemExit):
        transcribe.parse_args()


def test_transcribe_cli_distinguishes_automatic_and_explicit_worker_values(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["transcribe.py", "audio.m4a"])

    automatic = TranscribeOptions.from_args(transcribe.parse_args())

    assert automatic.num_workers is None
    assert automatic.cpu_threads is None

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "transcribe.py",
            "audio.m4a",
            "--num-workers",
            "1",
            "--cpu-threads",
            "1",
        ],
    )

    explicit = TranscribeOptions.from_args(transcribe.parse_args())

    assert explicit.num_workers == 1
    assert explicit.cpu_threads == 1


def test_default_model_path_requires_local_faster_whisper_model(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        transcribe,
        "DEFAULT_WHISPER_MODEL_DIR",
        workspace_tmp_path / "missing",
    )

    with pytest.raises(
        RuntimeError, match=r"scripts\.setup\.install_model --model faster-whisper"
    ):
        transcribe.default_model_path()


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
        "scripts.transcribe.parallel_asr.run_parallel_whisper_transcribe",
        return_value=(
            {"language": "zh"},
            [{"id": 0, "start": 0.0, "end": 1.0, "text": "测试"}],
            "faster-whisper",
        ),
    )

    with LoggingSession(workspace_tmp_path / "transcribe.log"):
        transcribe.run_transcribe(make_args(manifest_path, result_dir))

    assert capsys.readouterr().out == "[Stage] Transcribe audio with whisper\n"


def test_run_transcribe_uses_parallel_asr_for_whisper(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    resource_dir.mkdir(parents=True)
    audio_path = resource_dir / "BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(manifest_path, {"id": "BVTEST", "audio_files": [audio_path.as_posix()]})
    parallel_mock = mocker.patch(
        "scripts.transcribe.parallel_asr.run_parallel_whisper_transcribe",
        return_value=(
            {"language": "zh", "duration": 9.0},
            [{"id": 0, "start": 0.0, "end": 1.0, "text": "并行文本"}],
            "faster-whisper",
        ),
    )
    qwen3_mock = mocker.patch("scripts.transcribe.transcribe_with_qwen3")

    result = transcribe.run_transcribe(make_args(manifest_path, result_dir))

    parallel_mock.assert_called_once()
    qwen3_mock.assert_not_called()
    assert result["json_path"] == result_dir / "BVTEST_transcript.json"
    assert result["markdown_path"] == result_dir / "BVTEST_transcript.md"
    assert result["payload"]["segments"][0]["text"] == "并行文本"


def test_run_transcribe_uses_only_qwen3_for_explicit_qwen3_provider(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    resource_dir = result_dir / "resource"
    resource_dir.mkdir(parents=True)
    audio_path = resource_dir / "BVTEST.m4a"
    audio_path.write_bytes(b"audio")
    manifest_path = resource_dir / "fetch_manifest.json"
    write_json(manifest_path, {"id": "BVTEST", "audio_files": [audio_path.as_posix()]})
    args = make_args(manifest_path, result_dir)
    args.asr_provider = "qwen3"
    qwen3_mock = mocker.patch(
        "scripts.transcribe.transcribe_with_qwen3",
        return_value=(
            {"language": "zh"},
            [
                {"id": 0, "start": 0.0, "end": 5.0, "text": "第一段文本"},
                {"id": 1, "start": 5.0, "end": 10.0, "text": "第二段文本"},
            ],
        ),
    )
    parallel_mock = mocker.patch(
        "scripts.transcribe.parallel_asr.run_parallel_whisper_transcribe"
    )

    result = transcribe.run_transcribe(args)

    qwen3_mock.assert_called_once_with(
        audio_path,
        "zh",
        result_dir / "asr_qwen3",
    )
    parallel_mock.assert_not_called()
    assert result["payload"]["source"] == "qwen3-asr"
    assert len(read_json(result["json_path"])["segments"]) == 2
    markdown = result["markdown_path"].read_text(encoding="utf-8")
    assert "[00:00:00 - 00:00:05] 第一段文本" in markdown
    assert "[00:00:05 - 00:00:10] 第二段文本" in markdown
    assert "第一段文本，第二段文本" not in markdown
