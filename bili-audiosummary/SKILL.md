---
name: bili-audiosummary
description: Use this skill when the user provides a Bilibili/B站/BV video URL and wants an audio-based summary, notes, key points, timestamps, or asks what the video says, e.g. “总结这个B站视频”, “这个视频讲了什么”, “提炼要点”, or “生成笔记”. Do not use it for visual analysis, PV/music/dance videos, editing, comments, covers, or original-video downloads.
compatibility: Windows. Recommended uv for Python 3.12; otherwise a local Python 3.12 installation is required. Requires network access to Bilibili, PyPI, GitHub, and Hugging Face or configured mirrors.
license: Apache-2.0
metadata:
  Github: https://github.com/Apprentice-Geo/skills/tree/main/bili-audiosummary
---

# Bilibili Audio Summary

## Usage Scenarios

Use this skill when the user provides a Bilibili video URL and wants an audio-based summary, notes, key points, timestamps, or an explanation of what the video says.

Use it for audio-first videos such as talks, interviews, lectures, podcasts, news commentary, tutorials, and narrated explainers. Do not use it as the main solution when essential information is carried by video frames, on-screen text, charts, actions, or images because this skill does not analyze visual content.

The pipeline prefers usable subtitles and otherwise transcribes audio. faster-whisper is the default ASR provider. On a CUDA-capable machine with the optional models installed, Qwen3-ASR usually provides better transcription quality and efficiency; if it is unavailable or fails, the pipeline falls back to faster-whisper.

User-facing installation and cookie export instructions are in [README.md](README.md). Maintainer details are in [references/architecture.md](references/architecture.md).

## Main Steps

1. Confirm that the input is a Bilibili video URL and that an audio-based summary fits the request.
2. If `.venv` or required dependencies are missing, run:

```powershell
.\scripts\setup\setup_windows.bat
```

3. Run the pipeline:

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>"
```

Use `--language en` for English content, `--skip-subtitles` to force ASR, and `--asr-provider qwen3` only after the optional Qwen3 setup has completed.

4. Prefer dispatching a fresh subagent with no inherited parent conversation. Give it only the `Summary Prompt` path and the task of following that prompt, reading its linked transcript data, and writing the final summary. Do not pass the transcript content or parent conversation to the subagent.
5. If subagent delegation is unavailable, read the `Summary Prompt` in the current Agent and complete the same summary-writing task. Do not read other files unless the prompt links them or debugging is required.
6. The main Agent validates the final summary:

```powershell
.\.venv\Scripts\python.exe scripts\validate_summary.py "<summary-path>"
```

7. If a command fails, follow [references/error-handling.md](references/error-handling.md).

## Processing Time

Processing time depends on whether a valid subtitle or cached result is available, network and download speed, video length, and the selected ASR path.

Subtitle reuse is normally the shortest path. Downloading resources adds network-dependent time. CPU faster-whisper processing generally grows with video length and local CPU performance. On supported CUDA hardware, Qwen3-ASR can be more efficient, but model setup, loading, fallback, and available GPU resources also affect total time.
