# Error Handling

Use this reference only when a command fails or debugging is required.

## Setup Failures

- If setup fails because neither `uv` nor `py -3.12` is available, tell the user to install `uv` from `https://docs.astral.sh/uv/` and rerun `.\scripts\setup\setup_windows.bat`.
- If an existing `.venv` is not Python 3.12, stop. Do not delete it automatically.
- If pip, packaged ffmpeg, or model downloads fail, read the `.cache/logs/setup-*.log` path printed by setup and check mirror variables in `README.md`.
- If model downloads fail after the network and mirror settings look correct, verify that the `HF_HOME` and `HUGGINGFACE_HUB_CACHE` paths printed by setup are writable.
- If the default faster-whisper path fails, verify `.venv`, packaged ffmpeg, and `models/faster-whisper-small/`.

## Bilibili Fetch Failures

- Processing commands write complete details and tracebacks to a log. Before metadata identifies the video, read `.cache/logs/<command>-*.log`; afterward, read the log moved to `results/<BVID>/`.
- If Bilibili download fails, report the network or yt-dlp error and ask for a reachable URL if needed.
- If Bilibili returns `HTTP 412`, stop immediately. Do not query other sources or generate a summary. Tell the user to provide a Netscape-format cookie file by following the cookie export instructions in `README.md`.
- The pipeline auto-detects `cookies.txt`, `www.bilibili.com_cookies.txt`, and `bilibili_cookies.txt` in the skill root. Otherwise rerun with `--cookies .\cookies.txt`.

## Cache And Subtitle Issues

- When rerunning the same BVID, only cached subtitle `.srt` files matching the current requested language and still parsing correctly are reused directly.
- Other subtitle cache files do not block a fresh subtitle fetch attempt.
- If cached `.srt` files are unusable, the pipeline should try to refetch subtitles.
- Subtitle or audio download failures can be warnings during fetch. The main pipeline should continue when either usable subtitles or usable audio exists.

## ASR Failures

- If STT fails on the default path, verify `.venv`, packaged ffmpeg, and `models/faster-whisper-small/`.
- If `--asr-provider qwen3` was requested, check the transcript JSON `source` field to confirm whether the run actually used `qwen3-asr` or fell back to `faster-whisper`.
- If Qwen3 did not run, verify the machine has available CUDA and rerun `.\.venv\Scripts\python.exe scripts\setup\install_qwen3.py`. The model files belong under `models/qwen3-asr-0.6b/` and `models/qwen3-forcedaligner-0.6b/`.
- `--asr-provider qwen3` means try Qwen3-ASR first; it is not a strict Qwen3-only mode.

## Terminal Failure Cases

- If both subtitles and audio are unavailable, the pipeline should stop with a clear error instead of attempting STT.
- Treat the terminal as a concise status view. Use the `Full log` path printed on failure for BVID, manifest, metadata, transcript paths, cache decisions, fallback reasons, yt-dlp warnings, and traceback details.
- If the problem cannot be solved from local logs and README guidance, report the specific failure to the user.
