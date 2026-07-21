from __future__ import annotations

from dataclasses import dataclass

import numpy as np


SAMPLE_RATE = 16_000
MIN_CHUNK_SAMPLES = 30 * SAMPLE_RATE
MAX_CHUNK_SAMPLES = 180 * SAMPLE_RATE

BOUNDARY_SILENCE = "silence"
BOUNDARY_HARD = "hard"
BOUNDARY_AUDIO_END = "audio_end"
BOUNDARY_TYPES = {BOUNDARY_SILENCE, BOUNDARY_HARD, BOUNDARY_AUDIO_END}


@dataclass(frozen=True)
class ChunkLayout:
    index: int
    start_sample: int
    end_sample: int
    end_boundary: str
    estimated_speech_samples: int

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample


@dataclass(frozen=True)
class NormalizedAudio:
    samples: np.ndarray
    sample_rate: int = SAMPLE_RATE

    def __post_init__(self) -> None:
        samples = np.asarray(self.samples)
        if samples.ndim != 1:
            raise ValueError("Normalized audio must be mono.")
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)
            object.__setattr__(self, "samples", samples)
        if self.sample_rate != SAMPLE_RATE:
            raise ValueError(f"Normalized audio must use {SAMPLE_RATE} Hz.")

    @property
    def sample_count(self) -> int:
        return int(self.samples.size)

    @property
    def duration(self) -> float:
        return self.sample_count / self.sample_rate

    def slice(self, layout: ChunkLayout) -> np.ndarray:
        if not 0 <= layout.start_sample < layout.end_sample <= self.sample_count:
            raise ValueError("Chunk layout is outside normalized audio.")
        return self.samples[layout.start_sample : layout.end_sample]
