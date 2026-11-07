from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]

ASSETS_DIR = SKILL_ROOT / "assets"
SUMMARY_INSTRUCTIONS_PATH = ASSETS_DIR / "summary_instructions.md"

SUMMARY_LANGUAGES = {
    "zh": "中文",
    "en": "英文",
    "ja": "日语",
    "ru": "俄语",
    "fr": "法语",
    "de": "德语",
}
DEFAULT_SUMMARY_LANGUAGE = "zh"

SUBTITLE_LANGUAGE_PRIORITY = {
    "zh": [
        "zh-Hans",
        "zh-CN",
        "zh",
        "zh-Hant",
        "zh-TW",
        "ai-zh",
    ],
    "en": [
        "en",
        "en-US",
        "en-GB",
        "ai-en",
    ],
}

DEFAULT_AUDIO_CODEC = "best"
DEFAULT_AUDIO_SELECTOR = "worstaudio/worst[acodec!=none]/worst"

DEFAULT_SUBTITLE_LANGUAGE = "zh"
