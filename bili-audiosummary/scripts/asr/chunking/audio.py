from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from scripts.asr.chunking.types import NormalizedAudio, SAMPLE_RATE
from scripts.utils import path_to_posix


def decode_normalized_audio(audio_path: Path) -> NormalizedAudio:
    from faster_whisper import decode_audio

    samples = decode_audio(path_to_posix(audio_path), sampling_rate=SAMPLE_RATE)
    return NormalizedAudio(np.asarray(samples, dtype=np.float32), SAMPLE_RATE)


def detect_speech_samples(
    audio: NormalizedAudio,
    vad_parameters: Any,
) -> list[tuple[int, int]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(
        threshold=vad_parameters.threshold,
        neg_threshold=vad_parameters.neg_threshold,
        min_speech_duration_ms=vad_parameters.min_speech_duration_ms,
        min_silence_duration_ms=vad_parameters.min_silence_duration_ms,
        max_speech_duration_s=(
            math.inf
            if vad_parameters.max_speech_duration_s is None
            else vad_parameters.max_speech_duration_s
        ),
        speech_pad_ms=vad_parameters.speech_pad_ms,
    )
    timestamps = get_speech_timestamps(
        audio.samples,
        options,
        sampling_rate=audio.sample_rate,
    )
    return [(int(item["start"]), int(item["end"])) for item in timestamps]
