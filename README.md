# bili-audiosummary

该项目是一个 Agent Skill，遵循 [Agent Skills](https://agentskills.io/home) 开发标准。目标是根据 B 站视频音频的 STT 转写结果生成视频内容总结。

当前已实现：输入 B 站视频 URL，解析 BVID，下载最低可用音频流，使用 faster-whisper 生成带时间戳的转写结果，并拼接 instructions、总结模板和转写文本生成 summary prompt。

## 目录结构

```text
bili-audiosummary/
├─ SKILL.md
├─ README.md
├─ requirements.txt
├─ .gitignore
├─ .venv/                         # setup_windows.ps1 创建，本地虚拟环境
├─ tools/
│  ├─ bin/
│  │  └─ ffmpeg/                  # 便携 ffmpeg，系统已有 ffmpeg 时可不下载
│  ├─ cache/                      # 下载缓存
│  └─ models/
│     └─ faster-whisper-small/    # 默认 STT 本地模型
├─ scripts/
│  ├─ setup_windows.bat
│  ├─ setup_windows.ps1
│  ├─ run_pipeline.py             # 总入口：下载音频、执行 STT、生成 summary prompt
│  ├─ fetch_audio.py              # 解析 URL 并下载最低可用音频流
│  ├─ transcribe.py               # 使用 faster-whisper 生成转写结果
│  ├─ config.py
│  └─ utils.py
├─ assets/
│  ├─ summary_instructions.md     # 英文总结生成规则
│  └─ summary_template_zh.md      # 中文总结输出模板
└─ results/
   └─ <BVID>/
      ├─ resource/
      │  ├─ audio/
      │  │  └─ <BVID>.m4a
      │  ├─ video/                # 预留目录，当前默认不下载视频画面
      │  ├─ fetch_manifest.json
      │  ├─ metadata.json
      │  └─ metadata.raw.json
      ├─ <BVID>_transcript.json
      ├─ <BVID>_transcript.md
      └─ <BVID>_summary_prompt.md
```

`.venv/`、`tools/`、`results/` 是运行期产物，默认不纳入 Git。

## 当前流程

```text
URL
  -> yt-dlp 解析元信息和 BVID
  -> results/<BVID>/ 创建结果目录
  -> 下载最低可用音频流
  -> faster-whisper STT
  -> 输出 transcript.json 和 transcript.md
  -> 拼接 summary_instructions、summary_template 和 transcript
  -> 输出 <BVID>_summary_prompt.md
```

保存 URL 时会截断查询参数，只保留类似：

```text
https://www.bilibili.com/video/BV12kXmBCEDi/
```

## 环境配置

Windows 默认使用：

```powershell
.\scripts\setup_windows.ps1
```

脚本会：

- 在 Skill 根目录创建 `.venv/`
- 将 Python 依赖安装到 `.venv/`
- 优先检测系统 `ffmpeg/ffprobe`
- 系统缺失时下载便携版 ffmpeg 到 `tools/bin/ffmpeg/`
- 下载默认模型 `Systran/faster-whisper-small` 到 `tools/models/faster-whisper-small/`

国内网络可设置：

```powershell
$env:BILI_AUDIO_PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
$env:BILI_AUDIO_FFMPEG_URL="<自定义 ffmpeg zip 下载地址>"
$env:HF_ENDPOINT="https://hf-mirror.com"
```

## 使用方式

总入口：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

仅下载音频：

```powershell
.\.venv\Scripts\python.exe scripts\fetch_audio.py "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

仅执行 STT：

```powershell
.\.venv\Scripts\python.exe scripts\transcribe.py results/BV12kXmBCEDi/resource/fetch_manifest.json
```

## 依赖组件

- 视频元信息和音频下载：`yt-dlp`
- 音频处理：`ffmpeg`
- 语音转写：`faster-whisper`

## 当前限制

- 当前只实现中文总结模板。
- pipeline 只生成 summary prompt；最终 summary 由调用该 Skill 的 Agent 根据 prompt 写入。
- 当前不分析视频画面，只基于音频 STT。
