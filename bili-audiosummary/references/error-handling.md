# Error Handling

Use this reference only when setup or processing fails.

## Quick Index

- [Setup and Python](#setup-and-python)
- [Environment and Dependencies](#environment-and-dependencies)
- [Download and Network Failures](#download-and-network-failures)
- [HTTP 412 and Cookies](#http-412-and-cookies)
- [Subtitle Cache](#subtitle-cache)
- [ASR Failures](#asr-failures)
- [Unified ASR Cache and Resume](#unified-asr-cache-and-resume)
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
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --language zh --cookies .\cookies.txt
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
- Standalone `--num-workers` and `--cpu-threads` overrides are strict. Values must be positive, their product must not exceed `B = max(1, floor(cpu_count * 0.75))`, and the resolved worker count must admit a `30s-180s` chunk count divisible by that worker count. The command does not lower explicit values or switch back to automatic planning; an impossible configuration fails before model loading.
- Audio decode, planning VAD, invalid chunk layout, word mapping, or merge validation errors stop before a final transcript is written. Use the full log to identify the failing stage; the normal contracts are documented in [architecture.md](architecture.md#shared-asr-pipeline-invariants).
- For memory exhaustion, account for the decoded audio array in addition to provider and model working memory. Retry only after reducing competing memory use or choosing a supported lower-resource configuration.
- Do not claim a transcript or summary was produced when ASR terminated before writing transcript outputs.

## Unified ASR Cache and Resume

- The workspace is `results/<BVID>/asr_parallel/` for Whisper and `results/<BVID>/asr_qwen3/` for Qwen3. Both contain `asr_plan.json`, `vad_result.json`, `progress.json`, `chunk_results/`, `result.json`, and `metrics.json`.
- Missing, malformed, incompatible, text-mismatched, non-monotonic, or out-of-range cached word data is not reused. The current run regenerates the affected planning state or retranscribes only chunks without a valid compatible result.
- `vad_result.json` is required for cache reuse. Missing or unreadable JSON, schema/source/VAD-parameter mismatches, and invalid interval structure are logged with a stable reason and regenerated after decoding. Intervals must use sorted non-overlapping integer sample coordinates; the loader does not clamp, reorder, merge, or otherwise repair them.
- After VAD validation, the current execution policy derives layouts again. If they differ from `asr_plan.json`, the plan is rebuilt and all old chunk results are ignored. If an invalid VAD artifact is regenerated to the exact same layouts, the artifact is rewritten while compatible chunk results remain reusable. A decoded sample-count mismatch always regenerates VAD and the plan.
- Older cache schemas are incompatible with the current workspace contract. Do not edit cache JSON manually to force reuse; rerun and allow the pipeline to replace the affected state.
- Stale files can remain on disk while being counted as ignored. Their presence alone does not prove that the run reused them; use the log and current progress state to confirm cache decisions.
- Every program invocation gives chunks without a valid result a fresh state. Within that invocation, the first Whisper failure is retried once and the second failure stops merging. Rerunning gives that failed chunk a new one-retry budget while preserving other valid chunk results.
- A complete compatible plan, VAD artifact, and chunk cache skips audio decode, device checks, and model loading. A partial cache with valid VAD decodes once without repeating VAD, loads the selected model once, and preserves all valid chunk results. Provider request or execution-policy identity changes invalidate the unified plan and its results.

## Strict Qwen3 Provider

- `--asr-provider qwen3` is strict Qwen3-only mode. It never imports, initializes, or invokes whole-audio or parallel faster-whisper after a Qwen3 failure.
- Qwen3 requires an available CUDA GPU, optional dependencies, `models/qwen3-asr-0.6b/`, and `models/qwen3-forcedaligner-0.6b/`.
- Qwen3 provider parsing clips only a final word whose forced-aligner end exceeds the chunk duration by at most `0.1s`, and records a warning in the full log. Larger overruns, an out-of-range start, or an overrun on an earlier word remain alignment failures.
- Install or repair the optional environment with:

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

- Missing dependencies, models, or CUDA, and model loading, inference, or alignment errors propagate as the transcription failure. No fallback transcript is written and summary-prompt generation does not continue.
- A successful explicit Qwen3 transcript has `source: qwen3-asr`. To use faster-whisper after a Qwen3 failure, start a separate run with `--asr-provider whisper` or omit the provider option.
- Inspect `results/<BVID>/asr_qwen3/` and the full log when cached or partially completed Qwen3 work behaves unexpectedly. The normal Qwen3 scheduling, cache, and retry contracts are documented in [architecture.md](architecture.md#qwen3-invariants).

## Logs

- Setup logs are written to `.cache/logs/setup-<timestamp>.log`.
- Pipeline, fetch, subtitle, and transcription logs start in `.cache/logs/`.
- After metadata or an output directory identifies the result location, processing logs move to `results/<BVID>/`.
- During ASR, the full log records the selected provider and execution policy, plan/VAD/cache state, audio decode, safe model-preparation fields, reused and pending chunk counts, execution, merge, and the final success or failure summary. It does not record transcript text, model return objects, cookies, environment variables, or model internals.
- During parallel faster-whisper transcription, the terminal remains concise while the full log records each failed chunk attempt. The first failure is a warning with its original traceback and retry action; the second is an error with its original traceback and final-failure action.
- A failed Qwen3 batch is a warning with the batch number, chunk range, original traceback, and isolation action. Each chunk that also fails during isolation has one separate error traceback. The isolation traceback does not repeat the failed batch traceback.
- `progress.json` stores only `ExceptionType: message` for a failed chunk, never a traceback. The raised pipeline error maps every failed chunk to the same concise summary; a single failure retains its original exception as the Python cause, while multiple independent failures are not represented as one artificial cause.
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
