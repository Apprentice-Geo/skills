from pathlib import Path

import numpy as np
import pytest

from scripts import input_cache, transcribe
from scripts.asr.alignment import AlignedTranscript, AlignmentItem


@pytest.mark.usefixtures("installed_models")
@pytest.mark.parametrize("automatic", [False, True])
def test_complete_result_reuse_skips_decode_and_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, automatic: bool
) -> None:
    calls = {"decode": 0, "language": 0, "vad": 0, "engine": 0}

    def decode(_path):
        calls["decode"] += 1
        return np.zeros(16000, dtype=np.float32)

    def detect(_samples):
        calls["language"] += 1
        return "en"

    def vad(_audio, _parameters):
        calls["vad"] += 1
        return [(0, 16000)]

    def engine(_samples, _request, _execution):
        calls["engine"] += 1
        return AlignedTranscript("hello.", (AlignmentItem("hello.", 0, 0.5, None),))

    monkeypatch.setattr(transcribe, "_decode_audio", decode)
    monkeypatch.setattr(transcribe, "_detect_language", detect)
    monkeypatch.setattr("scripts.asr.chunking.detect_speech_samples", vad)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixture audio")
    options = dict(
        language=None if automatic else "en",
        provider="faster-whisper",
        results_dir=tmp_path / "results",
        decoder=decode,
        language_detector=detect,
        engine=engine,
    )
    first = transcribe.run_transcribe(audio, **options)
    second = transcribe.run_transcribe(audio, **options)
    assert first.manifest_path == second.manifest_path
    assert calls == {
        "decode": 1,
        "language": int(automatic),
        "vad": int(automatic),
        "engine": 1,
    }

    # 损坏的解析缓存不能掩盖解码；有效公共结果仍避免重复推理。
    cache = first.manifest_path.parent.parent / (
        "input-auto.json" if automatic else "input.json"
    )
    cache.write_text('{"sample_count": 1}', encoding="utf-8")
    assert (
        transcribe.run_transcribe(audio, **options).manifest_path == first.manifest_path
    )
    assert calls["decode"] == 2
    assert calls["engine"] == 1

    previous_version = input_cache.version
    monkeypatch.setattr(
        input_cache,
        "version",
        lambda name: "changed-decoder" if name == "av" else previous_version(name),
    )
    changed = transcribe.run_transcribe(audio, **options)
    assert changed.manifest_path != first.manifest_path
    assert calls["decode"] == 3
    assert calls["engine"] == 2

    if automatic:
        # 缓存命中仍核对实际语言模型安装，不能用旧结果绕过缺失模型。
        marker = (
            transcribe.MODELS_DIR
            / "lang-id-voxlingua107-ecapa"
            / ".model_identity.json"
        )
        marker.unlink()
        with pytest.raises(RuntimeError, match="identity marker"):
            transcribe.run_transcribe(audio, **options)
