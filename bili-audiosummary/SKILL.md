---
name: bili-audiosummary
description: Use this skill when the user provides a Bilibili/B站/BV video URL and wants an audio-based summary, notes, key points, timestamps, or asks what the video says, e.g. “总结这个B站视频”, “这个视频讲了什么”, “提炼要点”, or “生成笔记”. Do not use it for visual analysis, PV/music/dance videos, editing, comments, covers, or original-video downloads.
compatibility: Windows. Recommended uv for Python 3.12; otherwise a local Python 3.12 installation is required. Requires network access to Bilibili, PyPI, GitHub, and Hugging Face or configured mirrors.
license: Apache-2.0
metadata:
  Github: https://github.com/Apprentice-Geo/skills/tree/main/bili-audiosummary
---

# Bilibili Audio Summary

Use this skill when the user provides a Bilibili video URL and wants a content summary based on spoken audio.

This skill is intended for videos where audio carries most of the information, such as talks, interviews, lectures, podcasts, news commentary, tutorials, and narrated explainers.

Do not use this skill as the main solution for visual-first videos such as PVs, music videos, dance videos, silent edits, visual demonstrations, or videos where important information is mainly in on-screen text, charts, actions, or images. This skill does not analyze video frames.

## Execution Steps

1. Confirm the input is a Bilibili video URL and the user wants an audio-based summary.
2. Decide whether the video is audio-first. If the task requires video-frame, on-screen text, chart, action, or image analysis, explain that this skill cannot analyze frames.
3. Ensure the Windows environment is set up when `.venv` or required dependencies are missing:

```powershell
.\scripts\setup\setup_windows.bat
```

The launcher prefers `uv` to run the Python setup with Python 3.12. If `uv` is not installed, it falls back to `py -3.12`. Existing `.venv` directories are reused only when they use Python 3.12; setup stops without deleting any incompatible environment. If neither launcher is available, tell the user to install `uv` from the official documentation and rerun setup:

```text
https://docs.astral.sh/uv/
```

If the machine has an available CUDA GPU and you want to try Qwen3-ASR for potentially better Chinese ASR, install its optional dependencies and both local models:

```powershell
.\.venv\Scripts\python.exe scripts\setup\install_qwen3.py
```

4. Run the main pipeline with the Bilibili URL:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>"
```

Use ASR even when subtitles exist by skipping subtitle reuse/download:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>" --skip-subtitles
```

Tell the user that the default STT provider is faster-whisper. Also mention that Qwen3-ASR is available as an optional preferred path for machines with CUDA after the Qwen3 setup command has completed.

With available CUDA and the local Qwen3 models already prepared, you can ask the pipeline to try Qwen3-ASR first. If Qwen3-ASR is unavailable or fails, the code falls back to faster-whisper:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>" --asr-provider qwen3
```

If the video should be processed as English content, set the target language explicitly:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>" --language en
```

5. Read the `Summary Prompt` path printed by the pipeline. Do not read other files unless debugging a failure.
6. Generate the final summary by following that prompt.
7. Write the summary to the output path shown at the top of the prompt.
8. Verify the final summary:

```powershell
.\.venv\Scripts\python.exe scripts\validate_summary.py "<summary-path>"
```

9. If any command fails, read `references/error-handling.md` and follow the matching failure case.

## Scripts

- `scripts/setup/setup_windows.bat`: thin launcher that prefers `uv` with Python 3.12 and falls back to `py -3.12`.
- `scripts/setup/setup.py`: create or reuse a strict Python 3.12 `.venv`, install and verify core requirements, verify `ffmpeg-binaries-compat`, and download the default faster-whisper model.
- `scripts/setup/install_qwen3.py`: install optional CUDA Qwen3 dependencies and download both required Qwen3 models.
  Setup keeps complete command output in `.cache/logs/setup-<timestamp>.log`, while the terminal only shows short progress messages and failed-command output. Existing `UV_CACHE_DIR`, `HF_HOME`, `HUGGINGFACE_HUB_CACHE`, `PIP_INDEX_URL`, and `HF_ENDPOINT` values are preserved.
- `scripts/run_pipeline.py`: main entry point. Reuse matching cached subtitle `.srt` files only when they still parse correctly, otherwise best-effort download target-language subtitles; reuse cached audio when available, otherwise best-effort download audio; prefer subtitles when usable, then fall back to STT and generate the summary prompt.
- `scripts/process_logging.py`: shared logging configuration for setup, fetch, subtitle conversion, ASR, and the main pipeline. The terminal receives only key stages and final result paths. Complete logs move from `.cache/logs/pipeline-<timestamp>.log` to `results/<BVID>/pipeline-<timestamp>.log` after metadata identifies the video.
- `scripts/validate_summary.py`: validate that the final summary file exists, is UTF-8, and contains no template placeholders or template comments.
- `scripts/fetch_audio.py`: fetch Bilibili metadata plus target-language subtitles and audio; after the first metadata extraction, later subtitle and audio downloads use the canonical `https://www.bilibili.com/video/<BVID>/` URL. It reuses matching cached subtitle `.srt` files only when they still parse correctly, re-fetches subtitles when cached `.srt` files are unusable, reuses cached audio when available, and logs non-fatal subtitle or audio download warnings without printing them to the terminal.
- `scripts/transcribe.py`: transcribe an existing fetched audio manifest or audio file.

## Outputs

Pipeline outputs are written under:

```text
results/<BVID>/
```

Important files:

```text
resource/fetch_manifest.json
resource/metadata.json
resource/<BVID>.<audio-ext>
resource/subtitle/<BVID>.<lang>.srt
<BVID>_transcript.json
<BVID>_transcript.md
pipeline-<timestamp>.log
<BVID>_summary_prompt.md
<BVID>_summary_**.md
```

`<BVID>_summary_prompt.md` is generated by the pipeline. `<BVID>_summary_**.md` is the final summary file you create from that prompt.

## Error Handling

For setup, download, subtitle, ASR, cookie, cache, and terminal failure cases, read `references/error-handling.md` only when a command fails or debugging is required.

Important: if Bilibili returns `HTTP 412`, stop immediately. Do not query other sources or generate a summary. Ask the user to provide a Netscape-format cookie file by following the cookie export instructions in `README.md`.
