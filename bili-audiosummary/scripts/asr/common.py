from typing import Any


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
