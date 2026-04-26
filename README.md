# bili-audiosummary

该项目是一个 Agent Skill，遵循 [Agent Skills](https://agentskills.io/home) 开放标准。目标是优先复用符合目标语言的 B 站字幕，在字幕缺失或无效时回退到音频 STT，并基于 transcript 生成视频内容总结。

已实现：输入 B 站视频 URL，解析 BVID，下载目标语言字幕和最低可用音频流；若存在符合目标语言且可用的 `.srt` 字幕，则直接转换为统一 transcript；否则默认使用 faster-whisper 执行 STT，在本机有可用 CUDA 时也可选用 Qwen3-ASR，并拼接 instructions、总结模板和转写文本生成 summary prompt。

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
│  ├─ cache/                      # 预留本地缓存目录
│  └─ models/
│     ├─ faster-whisper-small/     # 默认 STT 本地模型
│     ├─ qwen3-asr-0.6b/           # 可选 Qwen3 ASR 本地模型
│     └─ qwen3-forcedaligner-0.6b/ # 可选 Qwen3 对齐模型
├─ scripts/
│  ├─ setup_windows.bat
│  ├─ setup_windows.ps1
│  ├─ run_pipeline.py             # 总入口：抓字幕/音频，优先用字幕，必要时执行 STT，生成 summary prompt
│  ├─ fetch_audio.py              # 解析 URL，下载目标语言字幕和音频
│  ├─ transcribe.py               # 使用 faster-whisper 生成转写结果
│  ├─ config.py
│  └─ utils.py
├─ assets/
│  ├─ summary_instructions.md     # 总结生成规则
│  ├─ summary_template_en.md      # 英文总结输出模板
│  └─ summary_template_zh.md      # 中文总结输出模板
└─ results/
   └─ <BVID>/
      ├─ resource/
      │  ├─ <BVID>.<audio-ext>
      │  ├─ fetch_manifest.json
      │  ├─ metadata.json
      │  ├─ metadata.raw.json
      │  └─ subtitle/
      │     └─ <BVID>.<lang>.srt
      ├─ <BVID>_transcript.json
      ├─ <BVID>_transcript.md
      └─ <BVID>_summary_prompt.md
```

## 当前流程

```text
URL
  -> yt-dlp 解析元信息和 BVID
  -> results/<BVID>/ 创建结果目录
  -> 优先复用符合目标语言且可正常解析的 .srt 本地字幕缓存；无匹配缓存或缓存损坏时尽力下载目标语言字幕
  -> 优先复用已下载音频；无缓存时尽力下载最低可用音频流
  -> 字幕可用时直接生成 transcript；无可用字幕或字幕无效时执行 ASR (default faster-whisper; optional Qwen3-ASR on CUDA)
  -> 若字幕和音频都不可用，则报错退出
  -> 输出 transcript.json 和 transcript.md
  -> 拼接 summary_instructions、summary_template 和 transcript
  -> 输出 <BVID>_summary_prompt.md
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
- 系统缺失时使用 `ffmpeg-binaries-compat` 随 Python 依赖安装的二进制
- 下载默认模型 `Systran/faster-whisper-small` 到 `tools/models/faster-whisper-small/`

默认 setup 只准备 faster-whisper。Qwen3-ASR 是有可用 CUDA 时的可选项，需要显式执行：

```powershell
.\scripts\setup_windows.ps1 -InstallQwen3
```

如需实际使用 Qwen3-ASR，还必须准备本地 Qwen3 模型：

```powershell
.\scripts\setup_windows.ps1 -DownloadQwen3Models
```

Qwen3 的可选依赖安装仍然继续兼容 `PIP_INDEX_URL` 和 `HF_ENDPOINT` 的国内镜像优化。其中 `torch` 与 `torchaudio` 会在启用 `-InstallQwen3` 时单独走 PyTorch 官方 CUDA wheel 源，避免从普通 PyPI 安装成 CPU 版；Qwen3 模型下载继续通过 `huggingface_hub` 完成。当前设计要求本地模型已存在后才能使用 Qwen3 运行转写。

setup 脚本会默认使用较稳定的 [PyPI 清华源](https://pypi.tuna.tsinghua.edu.cn/simple)与 [Hugging Face 镜像站](https://hf-mirror.com)。

需要使用原生源可在运行前设置：

```powershell
$env:PIP_INDEX_URL="https://pypi.org/simple/"
$env:HF_ENDPOINT="https://huggingface.co/"
```

ffmpeg 解析顺序为：系统 PATH 中的 `ffmpeg/ffprobe` -> `ffmpeg-binaries-compat`。

## 使用方式

如果遇到 B 站返回 `HTTP 412` 或其他需要登录态的情况，可先准备 `cookies.txt`。

推荐方式：

- Chrome：安装 `Get cookies.txt LOCALLY` 扩展，然后在已登录 B 站的情况下导出，文件会直接保存到下载目录。
  扩展地址：<https://chromewebstore.google.com/detail/get-cookiestxt-locally/>
- Edge：安装 `Cookie-Editor` 扩展，在已登录 B 站的情况下选择导出格式为 `Netscape`，扩展会将内容复制到剪贴板。随后新建一个 `cookies.txt` 文件，将内容粘贴并保存。
  扩展地址：<https://microsoftedge.microsoft.com/addons/detail/cookieeditor/>

准备好 `cookies.txt` 后，可在命令中加入：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "<bilibili-url>" --cookies .\cookies.txt
```

总入口：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

抓取元信息、字幕和音频：

```powershell
.\.venv\Scripts\python.exe scripts\fetch_audio.py "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

同一 `BVID` 重跑时：

- `fetch_audio.py` 会优先复用符合当前请求语言且可正常解析的 `.srt` 字幕缓存；缓存损坏时会尝试重新拉取字幕
- 已下载音频会直接复用
- 字幕或音频下载失败时先输出 warning，不会立即中止流程
- `run_pipeline.py` 只有在既没有可用字幕、也没有可用音频可供 STT 回退时才会报错退出

仅执行 STT：

```powershell
.\.venv\Scripts\python.exe scripts\transcribe.py results/BV12kXmBCEDi/resource/fetch_manifest.json
```

在本机有可用 CUDA，且本地 Qwen3 模型已下载完成时，可选使用 Qwen3-ASR：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/" --asr-provider qwen3
```

处理英文内容时，可显式指定目标语言：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py "https://www.bilibili.com/video/BV12kXmBCEDi/" --language en
```

Qwen3 路径内部固定使用：

- `Qwen/Qwen3-ASR-0.6B`
- `Qwen/Qwen3-ForcedAligner-0.6B`
- `device_map="cuda:0"`
- `dtype="bfloat16"`

Qwen3 的安装策略：

- 默认依赖继续走当前 `PIP_INDEX_URL`，例如清华源
- `torch` 和 `torchaudio` 在 `-InstallQwen3` 时单独从 PyTorch 官方 CUDA wheel 源安装
- Qwen3 模型和 aligner 仍通过 `huggingface_hub` 下载，继续受 `HF_ENDPOINT` 控制
- 使用 `--asr-provider qwen3` 前，需要先执行 `-InstallQwen3` 和 `-DownloadQwen3Models`

## 依赖组件

- 视频元信息、字幕和音频下载：`yt-dlp`
- 音频处理：`ffmpeg`或`ffmpeg-binaries-compat`
- 默认语音转写：`faster-whisper`
- 可选 CUDA 语音转写：`qwen-asr` + `torch` + `torchaudio` + `transformers` + `accelerate` + `huggingface_hub` + `numpy` + `soundfile` + `librosa`

## 能力边界

- 该 Skill 只生成 summary prompt；最终 summary 由调用该 Skill 的 Agent 根据 prompt 写入。
- 当前不分析视频画面；transcript 优先来自目标语言字幕，字幕不可用时才回退到音频 STT。
- 只支持 bilibili 平台的视频 URL。
- Qwen3-ASR 仅建议在本机有可用 CUDA，且本地模型已准备完成时启用；无 CUDA 或未准备模型时继续使用默认 faster-whisper。
