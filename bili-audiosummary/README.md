# bili-audiosummary

`bili-audiosummary` 是一个遵循 [Agent Skills](https://agentskills.io/home) 开放标准的 Agent Skill。它接收 Bilibili 视频 URL，基于可用字幕或音频转写生成 transcript 和 summary prompt，再由 Agent 完成最终总结。

## 功能亮点

- 音频优先：适合访谈、讲座、播客、教程、新闻评论和解说类视频。
- 字幕与 ASR：优先使用可用字幕，必要时通过本地 ASR 模型转写音频。
- 可选 Provider：默认使用 faster-whisper；满足 CUDA、依赖和模型要求时可显式选择 Qwen3-ASR。
- 中间结果复用：重复运行时会在条件匹配的情况下复用已有资源和处理结果。
- 统一产物：生成带时间戳的 transcript、summary prompt、最终总结路径和处理日志。

## 能力边界

- 总结仅依据字幕或 ASR 生成的 transcript，不分析视频画面。
- 不适合主要信息来自画面、图表、动作、屏幕文字或视觉演示的视频。
- 目前仅支持 Bilibili 视频 URL。
- pipeline 负责准备 transcript 和 summary prompt；最终 summary 由执行该 Skill 的 Agent 写入并校验。
- 显式选择 Qwen3-ASR 后，如果环境准备或转写失败，本次运行会停止，不会自动切换到 faster-whisper。

## 作为 Agent Skill 安装

按照所用 Agent 客户端的 Skill 安装说明，将本仓库中的 `bili-audiosummary` 目录完整安装、复制或链接到该客户端的 Skill 目录。安装时需保留 `SKILL.md`、`assets/`、`references/` 和 `scripts/` 等全部项目文件。

安装后，向 Agent 提供 Bilibili 视频 URL，并提出总结、笔记、要点提炼或时间戳整理等请求。Agent 会根据 [SKILL.md](SKILL.md) 执行流程。

## 在本地运行

本项目支持 Windows 和 Python 3.12，并统一使用 `uv` 管理解释器和依赖。在 Windows PowerShell 中进入项目目录，然后运行：

```powershell
.\scripts\setup\setup_windows.bat
```

默认 setup 会准备 Python 3.12 虚拟环境、核心依赖和项目使用的 ffmpeg。首次使用 ASR 前，还需要安装至少一种本地模型。默认 CPU 路径推荐 faster-whisper：

```powershell
uv run --no-sync python -m scripts.setup.install_model --model faster-whisper
```

运行完整 pipeline：

```powershell
uv run --no-sync python -m scripts.run_pipeline "https://www.bilibili.com/video/BV12kXmBCEDi/"
```

### 语言选项

使用 `--language` 指定字幕和转写语言，使用独立的 `--summary-language` 指定最终总结语言。例如，对英文内容生成中文总结：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --language en --summary-language zh
```

省略 `--summary-language` 时，最终总结语言跟随 transcript 语言。

### 强制使用 ASR

需要跳过字幕并直接转写音频时：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --skip-subtitles
```

### 使用 Qwen3-ASR

Qwen3-ASR 需要可用的 CUDA 环境、可选依赖和本地模型。先完成安装：

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

然后显式选择 Qwen3-ASR：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --asr-provider qwen3
```

该选项只运行 Qwen3-ASR。若依赖、模型、CUDA、模型加载、推理或对齐失败，本次转写会终止。如需改用 faster-whisper，请重新运行并省略 provider 参数，或显式传入 `--asr-provider whisper`。

### 生成和校验总结

pipeline 会打印 `Summary Prompt` 和 `Final Summary Path`。Agent 根据 prompt 写入最终 summary 后，应执行：

```powershell
uv run --no-sync python -m scripts.validate_summary "<summary-path>"
```

## ASR Benchmark

仓库提供显式运行的 benchmark，用于比较当前 ASR 路径的耗时和资源占用。运行前需要准备对应依赖、模型和测试视频所需的网络访问条件：

```powershell
uv run --no-sync python -m scripts.benchmark
```

需要局部重测时，可重复传入 `--video` 或 `--provider`：

```powershell
uv run --no-sync python -m scripts.benchmark --video BV1W694BEE7F --provider whisper
```

benchmark 的样本、计量范围、缓存和输出约定见 [项目架构](references/architecture.md#benchmark)。

## Cookies 导出

Bilibili 返回 `HTTP 412` 或请求需要登录态时，可准备 Netscape 格式的 cookie 文件。以下两种方法已经测试：

- Chrome：安装 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/)，登录 Bilibili 后导出，文件会保存到下载目录。
- Edge：安装 [Cookie-Editor](https://microsoftedge.microsoft.com/addons/detail/cookieeditor/)，登录 Bilibili 后选择 `Netscape` 格式导出，将剪贴板内容保存为 `cookies.txt`。

将文件放到项目根目录并命名为 `cookies.txt`、`www.bilibili.com_cookies.txt` 或 `bilibili_cookies.txt`，pipeline 会自动检测。也可以显式指定：

```powershell
uv run --no-sync python -m scripts.run_pipeline "<bilibili-url>" --cookies .\cookies.txt
```

Cookie 文件包含登录凭据，不应提交到版本控制，也不应复制到总结、日志说明或 benchmark 结果中。

## 进一步文档

- [项目架构](references/architecture.md)：pipeline 数据流、脚本职责、ASR 内部契约、缓存、产物和 benchmark 行为。
- [错误处理](references/error-handling.md)：setup、网络、Cookie、字幕、ASR、缓存和日志故障的排查与停止条件。

## 主要第三方依赖

- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)：解析 Bilibili 元信息并下载字幕和音频。
- [`ffmpeg-binaries-compat`](https://pypi.org/project/ffmpeg-binaries-compat/)：提供项目使用的 `ffmpeg` 和 `ffprobe`。
- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)：默认 ASR 引擎，可在 CPU 环境运行。
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)：可选 CUDA ASR 引擎。
- [`uv`](https://docs.astral.sh/uv/)：Python 3.12 环境和依赖管理入口。
