from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]

RESULTS_DIR = SKILL_ROOT / "results"
ASSETS_DIR = SKILL_ROOT / "assets"
SUMMARY_INSTRUCTIONS_PATH = ASSETS_DIR / "summary_instructions.md"
TOOLS_DIR = SKILL_ROOT / "tools"
TOOLS_BIN_DIR = TOOLS_DIR / "bin"
TOOLS_MODELS_DIR = TOOLS_DIR / "models"
DEFAULT_WHISPER_MODEL_DIR = TOOLS_MODELS_DIR / "faster-whisper-small"

PORTABLE_FFMPEG_BIN_DIR = TOOLS_BIN_DIR / "ffmpeg" / "bin"
PORTABLE_FFMPEG_EXE = PORTABLE_FFMPEG_BIN_DIR / "ffmpeg.exe"
PORTABLE_FFPROBE_EXE = PORTABLE_FFMPEG_BIN_DIR / "ffprobe.exe"

DEFAULT_SUBTITLE_LANGS = [
    "zh-Hans",
    "zh-CN",
    "zh",
    "zh-Hant",
    "zh-TW",
    "en",
]

DEFAULT_AUDIO_CODEC = "best"
DEFAULT_AUDIO_SELECTOR = "worstaudio/worst[acodec!=none]/worst"

DEFAULT_TRANSCRIBE_LANGUAGE = "zh"
DEFAULT_TRANSCRIBE_DEVICE = "cpu"
DEFAULT_TRANSCRIBE_COMPUTE_TYPE = "float32"
DEFAULT_TRANSCRIBE_BATCH_SIZE = 8
DEFAULT_TRANSCRIBE_BEAM_SIZE = 5

SUMMARY_TEMPLATE_BY_LANGUAGE = {
    "zh": ASSETS_DIR / "summary_template_zh.md",
}
