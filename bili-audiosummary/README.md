# bili-audiosummary

`bili-audiosummary` 是一个遵循 [Agent Skills](https://agentskills.io/home) 开放标准的 Agent Skill。它接收 Bilibili 视频 URL，优先使用可用字幕，在字幕不可用时通过 ASR 转写音频，并生成供 Agent 撰写最终总结的 summary prompt。

## 功能亮点

- 字幕优先：优先复用或下载目标语言字幕，减少不必要的 ASR。
- ASR Provider：默认使用 faster-whisper；具备 CUDA 环境时可显式选择严格的 Qwen3-ASR 路径。
- 统一产物：生成带时间戳的 transcript、summary prompt 和处理日志。
- 缓存复用：重跑时复用有效字幕、音频、本地模型，以及与完整计划匹配的 faster-whisper chunk 转写结果。
- 简洁终端输出：终端显示关键阶段、并行转写计划、chunk 进度与结果路径，完整细节写入日志。

## 能力边界

- 总结仅依据字幕或 ASR 生成的 transcript，不分析视频画面。
- 不适合主要信息来自画面、图表、动作、屏幕文字或视觉演示的视频。
- 目前仅支持 Bilibili 视频 URL。
- pipeline 生成 summary prompt；最终 summary 由执行该 Skill 的 Agent 根据 prompt 写入。
- Qwen3-ASR 需要可用的 CUDA 环境、可选依赖和额外模型；显式选择后若准备或转写失败，本次 ASR 直接失败，不会回退到 faster-whisper。

## 使用方式

### 作为 Agent Skill 安装

按照所用 Agent 客户端的 Skill 安装说明，将本仓库中的 `bili-audiosummary` 目录完整安装、复制或链接到该客户端的 Skill 目录。安装时需保留 `SKILL.md`、`assets/`、`references/` 和 `scripts/` 等全部项目文件。

安装后，向 Agent 提供 Bilibili 视频 URL，并提出总结、笔记、要点提炼或时间戳整理等请求。Agent 会根据 [SKILL.md](SKILL.md) 执行流程。

### Clone 后直接运行

在 Windows PowerShell 中进入项目目录，先完成默认环境配置：

```powershell
.\scripts\setup\setup_windows.bat
```

默认 setup 会准备 Python 3.12 虚拟环境、核心依赖和 `ffmpeg-binaries-compat`。uv 默认使用清华 PyPI 源；如需覆盖，可在运行前设置 `UV_DEFAULT_INDEX`。

首次使用前，需要至少下载一种本地 ASR 模型。默认 CPU 路径推荐下载 faster-whisper：

```powershell
uv run --no-sync python -m scripts.setup.install_model --model faster-whisper
```

随后运行：

```powershell
uv run --no-sync python -m scripts.run_pipeline "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

处理英文内容：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --language en
```

跳过字幕并强制使用 ASR：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --skip-subtitles
```

单独运行 faster-whisper 转写时，省略 `--num-workers` 和 `--cpu-threads` 会按音频长度与 CPU 预算自动规划：

```powershell
uv run --no-sync python -m scripts.transcribe --audio "<audio-path>" --output-dir "<result-dir>" --asr-provider whisper
```

也可以显式覆盖 worker 数和每个 model worker 的 CPU 线程数：

```powershell
uv run --no-sync python -m scripts.transcribe --audio "<audio-path>" --output-dir "<result-dir>" --asr-provider whisper --num-workers 1 --cpu-threads 1
```

只传一个覆盖参数时，另一个参数会在该约束下自动计算。显式值不会被静默降低；非正数、worker 超过 8、CPU 预算超限，或配置不适用于任一 macro 时，命令会在切片和模型加载前直接失败。

有可用 CUDA 时，可安装 Qwen3-ASR 的可选依赖和模型：

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

安装完成后严格使用 Qwen3-ASR：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --asr-provider qwen3
```

该选项只运行 Qwen3-ASR。依赖、模型、CUDA、模型加载、推理或对齐失败都会终止本次转写；如需使用 faster-whisper，请显式选择 `--asr-provider whisper` 或省略 provider 参数后重新运行。

pipeline 会打印 `Summary Prompt` 和 `Final Summary Path`。根据 prompt 写入最终 summary 后，可执行：

```powershell
uv run --no-sync python -m scripts.validate_summary "<summary-path>"
```

完整数据流、目录职责、脚本说明和输出位置见 [项目架构](references/architecture.md)。命令失败时见 [错误处理](references/error-handling.md)。

## Cookies 导出

Bilibili 返回 `HTTP 412` 或请求需要登录态时，可准备 Netscape 格式的 cookie 文件。以下两种方法已经测试：

- Chrome：安装 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/)，登录 Bilibili 后导出，文件会保存到下载目录。
- Edge：安装 [Cookie-Editor](https://microsoftedge.microsoft.com/addons/detail/cookieeditor/)，登录 Bilibili 后选择 `Netscape` 格式导出，将剪贴板内容保存为 `cookies.txt`。

将文件放到项目根目录并命名为 `cookies.txt`、`www.bilibili.com_cookies.txt` 或 `bilibili_cookies.txt`，pipeline 会自动检测。也可以显式指定：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --cookies .\cookies.txt
```

## 第三方依赖

- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)：解析 Bilibili 元信息并下载字幕和音频。
- [`ffmpeg-binaries-compat`](https://pypi.org/project/ffmpeg-binaries-compat/)：提供项目使用的 `ffmpeg` 和 `ffprobe`；不依赖系统 PATH 中的 ffmpeg。
- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)：默认 ASR 引擎，可在 CPU 环境运行。
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)：可选 CUDA ASR 引擎，配合 `Qwen/Qwen3-ASR-0.6B` 和 `Qwen/Qwen3-ForcedAligner-0.6B` 本地模型使用。
- [`uv`](https://docs.astral.sh/uv/)：唯一支持的 Python 3.12 环境与依赖同步入口。
