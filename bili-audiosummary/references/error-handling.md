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
- [Strict Qwen3 Provider](#strict-qwen3-provider)
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
- Standalone `--num-workers` and `--cpu-threads` overrides are strict. Values must be positive, their product must not exceed `B = max(1, floor(cpu_count * 0.75))`, and the resolved worker count must admit a `60s-300s` chunk count divisible by that worker count. The command does not lower explicit values or switch back to automatic planning; an impossible configuration fails before chunk files are written or the model is loaded.
- Silero VAD runs on 16kHz mono audio with `threshold=0.5`, `min_speech_duration_ms=250`, `min_silence_duration_ms=500`, and `speech_pad_ms=0`. No detected speech and continuous speech longer than 300 seconds are planning cases handled with hard boundaries, not VAD failures. Audio decode or VAD execution errors stop before chunk transcription.
- VAD decodes the complete audio and uses temporary sample arrays; allow roughly 0.5 GB of working memory per audio hour during planning. For multi-hour inputs, close memory-heavy programs or process the source on a machine with sufficient RAM.
- Merge rejects a segment whose end precedes its start. Genuine time-range intersections are combined, while endpoint contact remains separate; text similarity is never used to delete characters or words.
- Do not claim a transcript or summary was produced when ASR terminated before writing transcript outputs.

## Parallel faster-whisper Cache and Resume

- faster-whisper stores its workspace under `results/<BVID>/asr_parallel/`. Chunk audio uses `chunks/chunk_<index>.wav`; cached transcription results use `chunk_results/chunk_<index>.json`.
- A cached Schema 4 plan is reused only when the source-audio fingerprint, ASR parameters, VAD parameters, CPU budget, worker configuration, and final flat chunk layout all match. Plans and chunk results from earlier schemas are incompatible.
- If the cached plan is unreadable, structurally invalid, or incompatible, the current plan atomically replaces it and the progress state is rebuilt. Incompatible or unreadable chunk-result files are not deleted up front: a successful same-index chunk atomically replaces its stale file, while other stale files remain on disk, are counted as `ignored`, and are not reused.
- `[Transcribe] cache: reused=<n>, ignored=<n>, pending=<n>, total=<n>` reports the cache decision. Reused results and resumed chunks are also reported individually.
- Plan, progress, and chunk-result JSON files are written through a same-directory temporary file followed by `os.replace()`, so the target is always the previous or new complete JSON document.
- A valid chunk result is the source of truth and takes precedence over missing, stale, unreadable, or structurally invalid progress. Invalid progress is rebuilt from the current plan, then valid results are marked succeeded without loading a model for fully cached work.
- Every program invocation gives chunks without a valid result a fresh state: `pending`, `retry_count=0`, and no prior error. Within that invocation, the first failure is retried once and the second failure stops merging. Rerunning gives that failed chunk a new one-retry budget while preserving other valid chunk results.

## Strict Qwen3 Provider

- `--asr-provider qwen3` is strict Qwen3-only mode. It never imports, initializes, or invokes whole-audio or parallel faster-whisper after a Qwen3 failure.
- Qwen3 requires an available CUDA GPU, optional dependencies, `models/qwen3-asr-0.6b/`, and `models/qwen3-forcedaligner-0.6b/`.
- Install or repair the optional environment with:

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

- Missing dependencies, models, or CUDA, and model loading, inference, or alignment errors propagate as the transcription failure. No fallback transcript is written and summary-prompt generation does not continue.
- A successful explicit Qwen3 transcript has `source: qwen3-asr`. To use faster-whisper after a Qwen3 failure, start a separate run with `--asr-provider whisper` or omit the provider option.

## Logs

- Setup logs are written to `.cache/logs/setup-<timestamp>.log`.
- Pipeline, fetch, subtitle, and transcription logs start in `.cache/logs/`.
- After metadata or an output directory identifies the result location, processing logs move to `results/<BVID>/`.
- During parallel faster-whisper transcription, the terminal reports VAD and chunk planning, the resolved worker allocation, audio preparation, per-chunk cache/resume/success state, retry summaries, and merge completion.
- On failure, use the printed `Full log` path. It contains commands, BVID, manifest and metadata paths, cache decisions, ASR provider details, yt-dlp warnings, and tracebacks.
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
