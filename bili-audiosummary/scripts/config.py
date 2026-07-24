from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]

RESULTS_DIR = SKILL_ROOT / "results"
ASSETS_DIR = SKILL_ROOT / "assets"
SUMMARY_INSTRUCTIONS_PATH = ASSETS_DIR / "summary_instructions.md"

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

SUMMARY_TEMPLATE_BY_LANGUAGE = {
    "en": ASSETS_DIR / "summary_template_en.md",
    "zh": ASSETS_DIR / "summary_template_zh.md",
}
