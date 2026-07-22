from typing import Any


def make_segment(segment: Any) -> dict[str, Any]:
    return {
        "id": segment.id,
        "start": round(float(segment.start), 3),
        "end": round(float(segment.end), 3),
        "text": segment.text.strip(),
    }


def is_chinese_language(language: str) -> bool:
    return language.lower().startswith("zh")


def make_simplified_chinese_converter() -> Any:
    try:
        from opencc import OpenCC
    except ImportError as exc:
        raise RuntimeError(
            "Chinese transcription requires opencc-python-reimplemented to normalize output to Simplified Chinese. "
            r"Run .\scripts\setup\setup_windows.bat again to sync dependencies."
        ) from exc

    return OpenCC("t2s")


def normalize_segments_for_language(
    segments: list[dict[str, Any]],
    language: str,
) -> list[dict[str, Any]]:
    if not is_chinese_language(language):
        return segments

    converter = make_simplified_chinese_converter()
    for segment in segments:
        segment["text"] = converter.convert(str(segment.get("text") or ""))
        for word in segment.get("words") or []:
            word["word"] = converter.convert(str(word.get("word") or ""))
    return segments
