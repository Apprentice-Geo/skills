from __future__ import annotations

from pathlib import Path

from scripts.asr.chunking import (
    NormalizedAudio,
    decode_normalized_audio,
    detect_speech_samples,
)
from scripts.asr.parallel.plan import DEFAULT_VAD_PARAMETERS, VadParameters


def detect_speech_intervals(
    audio: NormalizedAudio,
    vad_parameters: VadParameters = DEFAULT_VAD_PARAMETERS,
) -> list[tuple[int, int]]:
    return detect_speech_samples(audio, vad_parameters)


def decode_audio(audio_path: Path) -> NormalizedAudio:
    return decode_normalized_audio(audio_path)
