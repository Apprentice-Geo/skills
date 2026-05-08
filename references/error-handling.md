# Error Handling

Use this reference only when a command fails or debugging is required.

## Setup Failures

- If setup fails because no compatible Python is available, tell the user to install `uv` from `https://docs.astral.sh/uv/` and rerun setup. For other setup failures, check the environment setup section in `README.md`.
- If pip, ffmpeg, or model downloads fail, check mirror variables in `README.md`.
- If the default faster-whisper path fails, verify `.venv`, ffmpeg, and `tools/models/faster-whisper-small/`.

## Bilibili Fetch Failures

- If Bilibili download fails, report the network or yt-dlp error and ask for a reachable URL if needed.
- If Bilibili returns `HTTP 412`, stop immediately. Do not query other sources or generate a summary. Tell the user to provide a Netscape-format cookie file by following the cookie export instructions in `README.md`.
- The pipeline auto-detects `cookies.txt`, `www.bilibili.com_cookies.txt`, and `bilibili_cookies.txt` in the skill root. Otherwise rerun with `--cookies .\cookies.txt`.

## Cache And Subtitle Issues

- When rerunning the same BVID, only cached subtitle `.srt` files matching the current requested language and still parsing correctly are reused directly.
- Other subtitle cache files do not block a fresh subtitle fetch attempt.
- If cached `.srt` files are unusable, the pipeline should try to refetch subtitles.
- Subtitle or audio download failures can be warnings during fetch. The main pipeline should continue when either usable subtitles or usable audio exists.

## ASR Failures

- If STT fails on the default path, verify `.venv`, ffmpeg, and `tools/models/faster-whisper-small/`.
- If `--asr-provider qwen3` was requested, check the transcript JSON `source` field to confirm whether the run actually used `qwen3-asr` or fell back to `faster-whisper`.
- If Qwen3 did not run, verify the machine has available CUDA and that both Qwen3 runtime dependencies and local model files were prepared under `tools/models/qwen3-asr-0.6b/` and `tools/models/qwen3-forcedaligner-0.6b/`.
- `--asr-provider qwen3` means try Qwen3-ASR first; it is not a strict Qwen3-only mode.

## Terminal Failure Cases

- If both subtitles and audio are unavailable, the pipeline should stop with a clear error instead of attempting STT.
- If the problem cannot be solved from local logs and README guidance, report the specific failure to the user.
