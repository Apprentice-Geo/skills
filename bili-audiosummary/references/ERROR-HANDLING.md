# Error Handling

Use this reference only when Bilibili resource preparation, job continuation, or summary completion fails. Transcription-model and cache failures belong to the separate `audio-transcribe` Skill.

## Quick Index

- [Setup and Dependencies](#setup-and-dependencies)
- [Download and Network Failures](#download-and-network-failures)
- [HTTP 412 and Cookies](#http-412-and-cookies)
- [Subtitle Selection](#subtitle-selection)
- [Job Status](#job-status)
- [External Transcription Input](#external-transcription-input)
- [Summary Completion](#summary-completion)
- [Logs](#logs)
- [Stop Conditions](#stop-conditions)

## Setup and Dependencies

- Run setup with `.\scripts\setup\setup_windows.bat`.
- If `uv` is unavailable, install it from <https://docs.astral.sh/uv/> and rerun setup.
- If an existing `.venv` does not use Python 3.12, stop. Do not delete or replace it automatically.
- If `.venv` is incomplete, remove or repair it only with explicit user approval, then rerun setup.
- Use `uv run --no-sync python` for commands after setup.
- If dependency sync fails, inspect the setup log and `pyproject.toml` / `uv.lock`.
- If `audio_transcribe_contract` cannot import, rerun setup; do not replace the pinned contract with copied Skill source.
- `ffmpeg-binaries-compat` is the supported ffmpeg source. If `ffmpeg` or `ffprobe` cannot be resolved, rerun setup rather than relying on system PATH.
- This setup does not install ASR dependencies or models. Follow `audio-transcribe` documentation when the job needs transcription.

## Download and Network Failures

- Inspect the full log for the original yt-dlp, network, timeout, or filesystem error.
- Confirm that the Bilibili URL is reachable and supported before retrying.
- Do not replace a failed Bilibili fetch with content from another source.
- Audio is required when no usable native subtitle exists or subtitles were explicitly skipped.
- A fatal error after the job entered `preparing` produces `failed` with a concise `error.stage/type/message`. It does not store traceback or cookie content.
- Do not manually change `failed` to another state. Repair the cause and rerun preparation without overwriting any valid existing job.

## HTTP 412 and Cookies

- If Bilibili returns `HTTP 412`, stop the current run. Do not query another source or generate a summary.
- Ask the user for a Netscape-format cookie file. Tested Chrome and Edge export procedures are in the Cookies section of [README.md](../README.md).
- The pipeline auto-detects `cookies.txt`, `www.bilibili.com_cookies.txt`, and `bilibili_cookies.txt` in the Skill root.
- For another filename or location:

```powershell
uv run --no-sync python -m scripts.run_pipeline `
  "<bilibili-url>" `
  --language zh `
  --cookies .\cookies.txt
```

- If cookies are rejected, confirm that they came from a logged-in Bilibili session, use Netscape format, and have not expired.
- Never copy cookie values into `summary_job.json`, a summary, or an error report.

## Subtitle Selection

- Only `.srt` files in the requested Bilibili language group and parsing into non-empty segments are reusable.
- A cue whose start and end timestamps are equal is logged and skipped. Other valid cues remain usable; if none remain, try another subtitle or fall back to audio transcription.
- Other subtitle files do not block a fresh target-language download attempt.
- An empty, malformed, or unreadable cached SRT should be logged and replaced when possible.
- `--skip-subtitles` intentionally bypasses both cached and downloaded subtitles and requires audio.
- If no valid subtitle remains but audio exists, a successful preparation ends at `needs_transcription`; this is not a failure.
- The Bilibili `--language` value is not an ASR language. Do not pass it to `audio-transcribe`.

## Job Status

Always read and validate the printed `summary_job.json`; do not infer success from generated filenames.

- `preparing`: preparation was interrupted before a stable state. Rerun or diagnose the preparation log.
- `needs_transcription`: invoke `audio-transcribe` with the job-relative audio path, then call `continue_summary` with the absolute manifest path.
- `prompt_ready`: write or repair the expected summary, then call `complete_summary`.
- `complete`: the source and summary were valid at completion. Repeating completion is allowed; do not overwrite the job.
- `failed`: preparation encountered a fatal error. Inspect its concise error and the full log.

Reject a job with a wrong schema, unknown status, missing fixed top-level keys, invalid nullability, absolute internal paths, or relative-path escape. Do not repair job JSON manually.

Preparation must not silently replace an existing `prompt_ready` or `complete` job. Confirm the exact result directory if a new request resolves to an existing BVID.

## External Transcription Input

Continue only from `needs_transcription`:

```powershell
uv run --no-sync python -m scripts.continue_summary `
  "<absolute-summary-job-path>" `
  --transcription-manifest "<absolute-result-manifest-path>"
```

The transcription manifest must be absolute. The pinned `audio-transcribe-contract` validates its complete status, schema, contained artifact paths, digests, identities, transcript segments, and raw timestamps. Continue then compares the SHA-256 of job `resources.audio` with `manifest.audio.id`. A contract or audio-identity failure must not publish `transcript.md`, a prompt, or an updated job.

On continue failure:

- keep the job at `needs_transcription` when prompt publication has not succeeded;
- do not edit, delete, or attempt to repair the external transcription directory;
- report the loading or path-safety reason and let the user or `audio-transcribe` workflow provide a usable result.

Calling continue again for an already bound transcription job with the same manifest revalidates the external manifest, the job audio identity, and the expected rendered Markdown before refreshing the prompt. A different manifest is rejected rather than silently replacing it. Existing ready or complete jobs are updated only when continue is explicitly called.

## Summary Completion

After writing the expected summary:

```powershell
uv run --no-sync python -m scripts.complete_summary "<absolute-summary-job-path>"
```

Completion fails without changing `prompt_ready` when:

- the summary file is missing or not valid UTF-8;
- template placeholders or prompt comments remain;
- required structure or language validation fails.

Fix the existing summary when appropriate and repeat completion. Native subtitle jobs still validate their transcript source. External transcription artifacts are not revalidated during completion. A valid summary produces `complete`, and repeating completion succeeds without rewriting the job.

## Logs

- Setup logs are written under `.cache/logs/`.
- Fetch and pipeline logs start there and move to `results/<BVID>/` after the result directory is known.
- Continue and complete commands print concise validation or state results. Use the pipeline log for preparation failures and the command output for later-stage failures.
- Logs must not record transcript text, cookie contents, or external model objects.
- `summary_job.error` stores only `stage`, exception type, and message; the full traceback belongs in the log.
- When reporting a failure, include the exact command, concise error, job path, and full-log path. Do not include secrets.

## Stop Conditions

Stop without generating or completing a summary when:

- the request requires visual analysis;
- the input is not a supported Bilibili URL;
- Bilibili returns `HTTP 412` and no valid cookie is available;
- neither a usable native subtitle nor usable audio is available;
- the job is `preparing`, `needs_transcription`, or `failed`;
- the job fails schema or path validation, or a required internal artifact cannot be read safely;
- the required prompt, template, transcript, or summary path cannot be safely resolved;
- transcription fails before producing a complete public manifest.

Never bypass a stop condition by editing the job status manually.
