---
name: bili-audiosummary
description: Use this skill when the user provides a Bilibili video URL(B站/BV链接) and wants an audio-based summary(音频总结), notes(笔记), key points(要点), timestamps(时间戳), or an explanation of what the video says(视频内容概述), e.g. summarize this Bilibili video(总结这个B站视频), what does this BV say(这个BV讲了什么), extract key points(提炼要点), or generate notes(生成笔记). Do not use it for visual analysis(画面分析), PV/music/dance videos(PV/音乐/舞蹈视频), editing(视频剪辑), comments(评论区), covers(封面), or original-video downloads(原视频下载).
compatibility: Windows. Requires uv for Python 3.12 environment management. Requires network access to Bilibili, PyPI, GitHub, and Hugging Face or configured mirrors.
license: Apache-2.0
metadata:
  Github: https://github.com/Apprentice-Geo/skills/tree/main/bili-audiosummary
---

# Bilibili Audio Summary

## Usage Scenarios

Use this skill when the user provides a Bilibili video URL (B站/BV链接) and wants an audio-based summary (音频总结), structured notes (结构化笔记), key points (要点提炼), timestamped notes (时间戳笔记), or an explanation of what the video says (视频内容概述).

Use it for audio-first videos (音频主导视频) such as talks, interviews, lectures, podcasts, news commentary, tutorials, and narrated explainers. Do not use it as the main solution for visual-first videos (画面主导视频) where essential information is carried by video frames, on-screen text, charts, actions, or images because this skill does not perform visual analysis (画面分析).

User-facing instructions (用户使用说明) for installation, configuration, and cookie export are in [README.md](README.md). Read [references/architecture.md](references/architecture.md) only for maintenance or debugging (维护或调试) that requires internal pipeline details.

## Main Steps

1. Confirm that the input is a Bilibili video URL (B站/BV链接) and that an audio-based summary (音频总结) fits the request.
2. If the runtime environment (运行环境) or required local ASR model (本地语音识别模型) is unavailable, follow the setup instructions in [README.md](README.md) before processing the video.
3. Run the pipeline from the Skill directory:

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --language <zh|en>
```

Choose the transcript language (转写语言) and final-summary language (最终总结语言) from the user's request. Use only documented options from [README.md](README.md); do not infer undocumented processing behavior (未记录的处理行为).

4. Read the `Summary Prompt` path (总结提示词路径) and `Final Summary Path` (最终总结路径) printed by the pipeline.
5. Prefer dispatching a fresh subagent (全新子代理) with no inherited parent conversation (不继承父对话). Give it only the summary prompt path and the task of following that prompt, reading its linked transcript data, and writing the final summary. Do not pass the transcript content or parent conversation to the subagent.
6. If subagent delegation (子代理委派) is unavailable, read the summary prompt in the current Agent and complete the same task. Do not read other files unless the prompt links them or debugging is required.
7. The main Agent (主代理) validates the final summary:

```powershell
uv run --no-sync python -m scripts.validate_summary "<summary-path>"
```

8. If a command fails, follow [references/error-handling.md](references/error-handling.md). Do not generate a summary when that reference specifies a stop condition (停止条件).

## Processing Time

Processing time (处理耗时) depends on whether a valid subtitle or cached result is available, network and download speed, video length, and the selected ASR path.

Subtitle or cache reuse (字幕或缓存复用) is usually faster than ASR (语音识别). When ASR is required, processing time generally grows with video length and depends on the available hardware. Avoid promising an exact completion time (不要承诺精确完成时间).
