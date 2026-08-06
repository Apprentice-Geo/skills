from __future__ import annotations

import argparse
from dataclasses import dataclass

from scripts.config import (
    DEFAULT_TRANSCRIBE_BEAM_SIZE,
    DEFAULT_TRANSCRIBE_COMPUTE_TYPE,
    DEFAULT_TRANSCRIBE_DEVICE,
)


@dataclass
class TranscribeOptions:
    model_path: str | None = None
    language: str | None = None
    device: str = DEFAULT_TRANSCRIBE_DEVICE
    compute_type: str = DEFAULT_TRANSCRIBE_COMPUTE_TYPE
    beam_size: int = DEFAULT_TRANSCRIBE_BEAM_SIZE
    cpu_threads: int | None = None
    num_workers: int | None = None
    max_chunk_seconds: float | None = None

    @classmethod
    def from_args(
        cls, args: argparse.Namespace | TranscribeOptions
    ) -> TranscribeOptions:
        if isinstance(args, cls):
            options = args
        else:
            options = cls(
                model_path=getattr(args, "model_path", None),
                language=getattr(args, "language", None),
                device=getattr(args, "device", DEFAULT_TRANSCRIBE_DEVICE),
                compute_type=getattr(
                    args, "compute_type", DEFAULT_TRANSCRIBE_COMPUTE_TYPE
                ),
                beam_size=getattr(args, "beam_size", DEFAULT_TRANSCRIBE_BEAM_SIZE),
                cpu_threads=getattr(args, "cpu_threads", None),
                num_workers=getattr(args, "num_workers", None),
                max_chunk_seconds=getattr(args, "max_chunk_seconds", None),
            )
        if isinstance(options.beam_size, bool) or not isinstance(
            options.beam_size, int
        ):
            raise ValueError("beam_size must be an integer")
        if options.beam_size <= 0:
            raise ValueError("beam_size must be positive")
        return options
