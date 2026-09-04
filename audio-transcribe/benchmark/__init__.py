from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "benchmark" / "data"
REFERENCE_MANIFEST = ROOT / "benchmark" / "references" / "manifest.json"
TMP_DIR = ROOT / "benchmark" / "tmp"

MODES = ("project-slicing", "provider-native")
PROVIDERS = ("faster-whisper", "qwen3-asr")
LANGUAGES = ("zh", "en")
MINUTES = (8, 16, 32, 64)
DEFAULT_REPETITIONS = 3
