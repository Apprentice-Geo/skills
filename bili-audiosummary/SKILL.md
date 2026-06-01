---
name: bili-audiosummary
description: Use this skill when the user provides a Bilibili/B站/BV video URL and wants an audio-based summary, notes, key points, timestamps, or asks what the video says, e.g. “总结这个B站视频”, “这个视频讲了什么”, “提炼要点”, or “生成笔记”. Do not use it for visual analysis, PV/music/dance videos, editing, comments, covers, or original-video downloads.
compatibility: Windows with PowerShell. Recommended uv for Python 3.12 environment creation; otherwise local Python >= 3.12 is required. Requires network access to Bilibili, PyPI, GitHub, and Hugging Face or configured mirrors.
license: Apache-2.0
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
.\scripts\setup_windows.ps1
```

The setup script prefers `uv` and uses it to create a Python 3.12 `.venv`. If `uv` is not installed, it falls back to a local Python >= 3.12. If neither is available, tell the user to install `uv` from the official documentation and rerun setup:

```text
https://docs.astral.sh/uv/
```

If the machine has an available CUDA GPU and you want to try Qwen3-ASR for potentially better Chinese ASR, install its optional runtime dependencies first:

```powershell
.\scripts\setup_windows.ps1 -InstallQwen3
```

Before actually using Qwen3-ASR, prepare the Qwen3 local model files:

```powershell
.\scripts\setup_windows.ps1 -DownloadQwen3Models
```

4. Run the main pipeline with the Bilibili URL:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>"
```

Use ASR even when subtitles exist by skipping subtitle reuse/download:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>" --skip-subtitles
```

Tell the user that the default STT provider is faster-whisper. Also mention that Qwen3-ASR is available as an optional preferred path for machines with CUDA after `-InstallQwen3` and `-DownloadQwen3Models` have been run.

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

- the file exists at the output path specified in the prompt
- no `{{...}}` placeholders remain
- template comments are not included
- the full transcript is not copied into the summary
- the file is saved as UTF-8

9. If any command fails, read `references/error-handling.md` and follow the matching failure case.

## Scripts

- `scripts/setup_windows.ps1`: prepare a Python >= 3.12 `.venv`, preferring `uv` with Python 3.12 and falling back to local Python >= 3.12 when `uv` is unavailable; install Python dependencies, resolve ffmpeg through system PATH or `ffmpeg-binaries-compat`, and download the default faster-whisper model. `-InstallQwen3` installs optional Qwen3 runtime dependencies, and `-DownloadQwen3Models` downloads the required Qwen3 local model files.
  When `UV_CACHE_DIR` is not already configured, setup uses `tools/uv-cache/` as the local uv cache directory.
  When `-InstallQwen3` is used, `torch` and `torchaudio` are installed from the PyTorch CUDA wheel index, while the remaining Qwen3 dependencies still use the configured pip mirror.
- `scripts/setup_windows.bat`: wrapper for the PowerShell setup script.
- `scripts/run_pipeline.py`: main entry point. Reuse matching cached subtitle `.srt` files only when they still parse correctly, otherwise best-effort download target-language subtitles; reuse cached audio when available, otherwise best-effort download audio; prefer subtitles when usable, then fall back to STT and generate the summary prompt.
- `scripts/fetch_audio.py`: fetch Bilibili metadata plus target-language subtitles and audio; after the first metadata extraction, later subtitle and audio downloads use the canonical `https://www.bilibili.com/video/<BVID>/` URL. It reuses matching cached subtitle `.srt` files only when they still parse correctly, re-fetches subtitles when cached `.srt` files are unusable, reuses cached audio when available, and only prints warnings when subtitle or audio download fails.
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
<BVID>_summary_prompt.md
<BVID>_summary_**.md
```

`<BVID>_summary_prompt.md` is generated by the pipeline. `<BVID>_summary_**.md` is the final summary file you create from that prompt.

## Error Handling

For setup, download, subtitle, ASR, cookie, cache, and terminal failure cases, read `references/error-handling.md` only when a command fails or debugging is required.

Important: if Bilibili returns `HTTP 412`, stop immediately. Do not query other sources or generate a summary. Ask the user to provide a Netscape-format cookie file by following the cookie export instructions in `README.md`.
