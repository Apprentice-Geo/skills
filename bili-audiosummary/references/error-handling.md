# Error Handling

Use this reference only when setup or processing fails.

## Quick Index

- [Setup and Python](#setup-and-python)
- [Environment and Dependencies](#environment-and-dependencies)
- [Download and Network Failures](#download-and-network-failures)
- [HTTP 412 and Cookies](#http-412-and-cookies)
- [Subtitle Cache](#subtitle-cache)
- [ASR Failures](#asr-failures)
- [Parallel faster-whisper Cache and Resume](#parallel-faster-whisper-cache-and-resume)
- [Qwen3 Fallback](#qwen3-fallback)
- [Logs](#logs)
- [Stop Conditions](#stop-conditions)

## Setup and Python

- Run setup with `.\scripts\setup\setup_windows.bat`.
- If `uv` is unavailable, install it from <https://docs.astral.sh/uv/> and rerun setup.
- If an existing `.venv` does not use Python 3.12, stop. Do not delete or replace it automatically.
- If `.venv` exists but is incomplete, remove or repair it only with explicit user approval, then rerun setup.
- Use `uv run --no-sync python` for processing commands after setup.
- Before ASR use, install at least one model with `uv run --no-sync python -m scripts.setup.install_model --model faster-whisper` or `--model qwen3`.

## Environment and Dependencies

- Setup preserves existing `UV_CACHE_DIR`, `UV_DEFAULT_INDEX`, `HF_HOME`, `HUGGINGFACE_HUB_CACHE`, and `HF_ENDPOINT` values. Check them in the setup log when cache or mirror behavior is unexpected.
- Without explicit overrides, setup uses `.cache/uv/`, the configured uv default index, `.cache/huggingface/`, and the configured Hugging Face endpoint.
- If dependency sync fails, inspect the `uv sync` output and `pyproject.toml` / `uv.lock` dependency constraints.
- If model downloads fail after network settings are correct, verify that `HF_HOME` and `HUGGINGFACE_HUB_CACHE` are writable.
- `ffmpeg-binaries-compat` is the only supported ffmpeg source. If `ffmpeg` or `ffprobe` cannot be resolved, rerun setup; do not rely on system PATH as a substitute.
- For default ASR environment failures, verify `.venv`, core imports, packaged ffmpeg, and `models/faster-whisper-small/`.

## Download and Network Failures

- Inspect the full log for the original yt-dlp, network, timeout, mirror, or model-download error.
- Confirm that the Bilibili URL is reachable and supported before retrying.
- Subtitle and audio downloads are best effort. A failure in one path is not fatal when the other path still provides enough input to build a transcript.
- Do not replace a failed Bilibili fetch with content from another source.

## HTTP 412 and Cookies

- If Bilibili returns `HTTP 412`, stop the current run immediately. Do not query other sources and do not generate a summary.
- Ask the user for a Netscape-format cookie file. The tested Chrome and Edge export procedures are documented in [README.md](../README.md#cookies-导出).
- The pipeline auto-detects `cookies.txt`, `www.bilibili.com_cookies.txt`, and `bilibili_cookies.txt` in the Skill root.
- For another filename or location, rerun explicitly:

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --cookies .\cookies.txt
```

- If cookies are still rejected, confirm that the export came from a logged-in Bilibili session, uses Netscape format, and has not expired.

## Subtitle Cache

- Only cached `.srt` files matching the requested language group and still parsing correctly are reused.
- Other subtitle files do not block a fresh target-language download attempt.
- If a cached SRT is empty, malformed, or unreadable, the pipeline should log the reason and try to download a replacement.
- If no valid subtitle remains but usable audio exists, continue with ASR.
- If a supposedly valid subtitle fails during conversion, inspect its encoding and SRT timestamp structure in the full log.

## ASR Failures

- If faster-whisper fails, verify that the audio path in `fetch_manifest.json` exists and that packaged ffmpeg and `models/faster-whisper-small/` are available.
- Check the transcript command log for model-loading, audio-decoding, memory, or language-normalization errors.
- For Chinese transcription failures involving OpenCC, rerun core setup to restore `opencc-python-reimplemented`.
- Do not claim a transcript or summary was produced when ASR terminated before writing transcript outputs.

## Parallel faster-whisper Cache and Resume

- faster-whisper stores its workspace under `results/<BVID>/asr_parallel/`. Cached transcription applies to `chunk_results/`; the WAV files under `chunks/` are regenerated on each run.
- A cached plan is reused only when its schema 2 plan exactly matches the current source-audio fingerprint, ASR options, CPU and worker settings, overlap, and chunk layout.
- If the terminal prints `[Transcribe] cached plan incompatible; rebuilding`, the current plan replaces the old plan. Existing incompatible or unreadable chunk-result files remain on disk but are counted as `ignored` and are not reused.
- `[Transcribe] cache: reused=<n>, ignored=<n>, pending=<n>, total=<n>` reports the cache decision. Reused results and resumed chunks are also reported individually.
- A valid atomic chunk result takes precedence over stale progress and is marked succeeded. A chunk left `running` by interruption returns to pending; a retryable failed chunk resumes with its recorded retry count.
- Each chunk is retried at most once. A chunk that has exhausted its retry remains failed and prevents transcript merging until a later run has a valid result.

## Qwen3 Fallback

- `--asr-provider qwen3` means try Qwen3-ASR first; it is not a strict Qwen3-only mode.
- Qwen3 requires an available CUDA GPU, optional dependencies, `models/qwen3-asr-0.6b/`, and `models/qwen3-forcedaligner-0.6b/`.
- Install or repair the optional environment with:

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

- If Qwen3 is unavailable or fails, the terminal prints a short fallback warning and the full reason is recorded in the log. The pipeline then attempts faster-whisper.
- Check the transcript JSON `source` field: `qwen3-asr` confirms Qwen3 completed; `faster-whisper` confirms fallback.
- Treat the run as failed only if the fallback also fails or no transcript is written.

## Logs

- Setup logs are written to `.cache/logs/setup-<timestamp>.log`.
- Pipeline, fetch, subtitle, and transcription logs start in `.cache/logs/`.
- After metadata or an output directory identifies the result location, processing logs move to `results/<BVID>/`.
- During parallel faster-whisper transcription, the terminal reports the plan, macro worker allocation, audio preparation, per-chunk cache/resume/success state, retry summaries, and merge completion.
- On failure, use the printed `Full log` path. It contains commands, BVID, manifest and metadata paths, cache decisions, ASR provider details, fallback reasons, yt-dlp warnings, and tracebacks.
- Chunk failures are concise in the terminal; their full tracebacks are available only in the log. The terminal is not the complete diagnostic record.

## Stop Conditions

Stop without generating a summary when any of these conditions applies:

- The request requires visual analysis that this Skill cannot perform.
- The input is not a supported Bilibili video URL.
- Bilibili returns `HTTP 412` and no valid cookie file is available.
- Both usable subtitles and usable audio are unavailable.
- Transcript generation fails and no transcript output is written.
- The summary prompt or required templates cannot be read.

When local logs do not provide enough information, report the exact failing command, error, and full-log path to the user.
