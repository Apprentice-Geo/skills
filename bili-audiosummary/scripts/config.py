from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]

RESULTS_DIR = SKILL_ROOT / "results"
ASSETS_DIR = SKILL_ROOT / "assets"
SUMMARY_INSTRUCTIONS_PATH = ASSETS_DIR / "summary_instructions.md"
MODELS_DIR = SKILL_ROOT / "models"
DEFAULT_WHISPER_MODEL_DIR = MODELS_DIR / "faster-whisper-small"
QWEN3_ASR_MODEL_DIR = MODELS_DIR / "qwen3-asr-0.6b"
QWEN3_ALIGNER_MODEL_DIR = MODELS_DIR / "qwen3-forcedaligner-0.6b"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"

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

DEFAULT_TRANSCRIBE_LANGUAGE = "zh"
DEFAULT_TRANSCRIBE_DEVICE = "cpu"
DEFAULT_TRANSCRIBE_COMPUTE_TYPE = "float32"
DEFAULT_TRANSCRIBE_BEAM_SIZE = 5
DEFAULT_ASR_PROVIDER = "whisper"

QWEN3_ASR_MODEL_REPO = "Qwen/Qwen3-ASR-0.6B"
QWEN3_ALIGNER_MODEL_REPO = "Qwen/Qwen3-ForcedAligner-0.6B"
QWEN3_DEVICE_MAP = "cuda:0"
QWEN3_DTYPE = "bfloat16"
QWEN3_MAX_INFERENCE_BATCH_SIZE = 4
QWEN3_MAX_NEW_TOKENS = 1024

SUMMARY_TEMPLATE_BY_LANGUAGE = {
    "en": ASSETS_DIR / "summary_template_en.md",
    "zh": ASSETS_DIR / "summary_template_zh.md",
}
