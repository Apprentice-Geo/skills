from __future__ import annotations

import unicodedata

from opencc import OpenCC

TEXT_NORMALIZATION_POLICY = {
    "schema_version": 1,
    "unicode_normalization": "NFKC",
    "zh_conversion": "OpenCC t2s",
}

_T2S = OpenCC("t2s")


def normalize_transcript_text(text: str, language: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return _T2S.convert(normalized) if language == "zh" else normalized
