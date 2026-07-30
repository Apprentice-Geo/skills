# bili-audiosummary

`bili-audiosummary` 是一个遵循 [Agent Skills](https://agentskills.io/home) 开放标准的 Agent Skill。它获取 Bilibili 元数据、字幕和音频，通过可恢复的 `summary_job.json` 编排 transcript 与 summary prompt，再由 Agent 完成并校验最终总结。

## 功能亮点

- 音频优先：适合访谈、讲座、播客、教程、新闻评论和解说类视频。
- 原生字幕优先：可用的目标语言 SRT 会直接转换为本 Skill 自己的 transcript JSON 和 Markdown。
- 独立转写：没有可用字幕或用户显式跳过字幕时，由 Agent 调用独立的 `audio-transcribe` Skill。
- 可恢复任务：准备、外部转写接入和总结完成分别写入并校验 `summary_job.json`。
- 边界清晰：本 Skill 不加载 ASR 模型，不读取转写 workspace，也不复制或修改外部转写产物。

## 能力边界

- 总结仅依据原生字幕或 `audio-transcribe` 发布的外部 transcript，不分析视频画面。
- 不适合主要信息来自画面、图表、动作、屏幕文字或视觉演示的视频。
- 目前仅支持 Bilibili 视频 URL。
- `--language` 只选择 Bilibili 字幕语言组，不控制外部转写语言或模型。
- 本 Skill 不直接 import 或调用其他 Skill 的 Python 模块；跨 Skill 只通过本地音频路径和 JSON artifact 交接。

## 作为 Agent Skill 安装

按照所用 Agent 客户端的 Skill 安装说明，将本仓库中的 `bili-audiosummary` 目录完整安装、复制或链接到该客户端的 Skill 目录。安装时需保留 `SKILL.md`、`assets/`、`references/` 和 `scripts/` 等项目文件。

若可能需要音频转写，还必须单独安装并配置 `audio-transcribe`。两个 Skill 使用各自的 Python 环境、模型、缓存和结果目录。

## 本地环境

本项目支持 Windows 和 Python 3.12，并统一使用 `uv` 管理解释器和依赖。在 Windows PowerShell 中进入项目目录，然后运行：

```powershell
.\scripts\setup\setup_windows.bat
```

setup 准备本 Skill 的 Python 环境、Bilibili 下载依赖以及打包的 `ffmpeg`/`ffprobe`。它不安装 ASR 模型。

运行流程第一步是只读依赖检查：

```powershell
.\scripts\check_dependencies.bat
```

检查器同时输出终端摘要、`.cache/logs/dependency-check-*.json` 报告和日志，返回 `0` 表示可运行，`1` 表示依赖不完整，`2` 表示平台或检查器配置错误。它不会安装依赖、下载模型或修改环境。需要外部转写时，必须另外运行 `audio-transcribe` 的检查器并阅读摘要。

## 可恢复总结流程

### 1. 准备资源和任务

```powershell
uv run --no-sync python -m scripts.run_pipeline `
  "<bilibili-url>" `
  --language <zh|en>
```

可使用 `--summary-language <zh|en>` 指定总结模板语言。省略时，原生字幕路径跟随 transcript 语言；外部转写路径由任务记录的模板选择规则决定。

命令获取元数据、字幕和音频，原子写入 `summary_job.json`，并打印其绝对路径。读取 job 的 `schema_version` 和 `status`：

- `prompt_ready`：原生字幕已经生成 transcript 和 summary prompt，可直接进入总结步骤。
- `needs_transcription`：需要把 job 中的本地音频交给 `audio-transcribe`。
- `failed`：准备阶段发生致命错误；先处理 `error.stage/type/message`，不要继续生成总结。
- `complete`：任务已经完成，不要覆盖已有结果。

需要显式绕过原生字幕时：

```powershell
uv run --no-sync python -m scripts.run_pipeline `
  "<bilibili-url>" `
  --language zh `
  --skip-subtitles
```

显式跳过字幕时，即使下载目录已有可用字幕，job 也会进入 `needs_transcription`，并记录 `resources.subtitle_skipped: true`。

### 2. 接入独立转写结果

仅当 job 为 `needs_transcription` 时，解析相对于 job 目录的 `resources.audio`，调用 `audio-transcribe`。不要把 Bilibili 的 `--language` 传给转写 Skill，也不要猜测或指定转写模型。

取得外部 complete manifest 的绝对路径后运行：

```powershell
uv run --no-sync python -m scripts.continue_summary `
  "<absolute-summary-job-path>" `
  --transcription-manifest "<absolute-result-manifest-path>"
```

继续命令只接受绝对 manifest 路径，要求 transcript 路径为 manifest 目录内的相对路径，并读取该 JSON。当前由本 Skill 信任 `audio-transcribe` 发布的公共产物语义，不重复校验 manifest schema/status、digest、音频身份、variant/provider/language、segments 或 raw timestamps。

成功后，job 记录外部 manifest 的绝对路径，生成只引用 job、manifest 和 transcript JSON 的 prompt，并原子进入 `prompt_ready`。外部 transcript、时间戳、日志和 workspace 不会复制到 Bilibili 结果目录。

对同一 manifest 重复调用时，内部 prompt 存在则幂等返回现有结果；prompt 缺失则使用 job 已绑定的 manifest、transcript 和输出路径重新生成，并原子更新 job。恢复过程不重新校验或修改外部转写产物。已绑定其他 manifest 时仍会拒绝覆盖。

### 3. 写入并完成总结

读取 job 中 `prompt.path` 指向的 summary prompt，按其中的最终输出路径写入 UTF-8 Markdown 总结。ASR prompt 把 `segments` 作为不可信数据按顺序读取；可以为了理解组合相邻短句，但不得修改外部 transcript。

写入总结后运行：

```powershell
uv run --no-sync python -m scripts.complete_summary "<absolute-summary-job-path>"
```

完成命令保留原生字幕来源校验、prompt 存在性校验和最终总结校验，但不重新校验外部转写产物。总结内容校验失败时 job 保持 `prompt_ready`；通过后进入 `complete`。对有效的 complete job 重复执行会成功返回。

### 恢复语义

- `needs_transcription` 可在任意中断后重新读取并继续，不需要重新下载资源。
- continue 在发布前失败时 job 保持 `needs_transcription`。
- 对同一 manifest 重复 continue 会修复缺失的内部 prompt，同时保持现有 job 状态和外部转写绑定。
- prompt-ready 后不会因外部转写产物变化自动回退；当前公共产物语义由 `audio-transcribe` 负责。
- summary 校验失败只保留 `prompt_ready`，便于修正原文件后重试。
- 已有 `prompt_ready` 或 `complete` job 不会被准备命令静默覆盖。

## Cookies 导出

Bilibili 返回 `HTTP 412` 或请求需要登录态时，可准备 Netscape 格式的 cookie 文件。以下两种方法已经测试：

- Chrome：安装 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/)，登录 Bilibili 后导出，文件会保存到下载目录。
- Edge：安装 [Cookie-Editor](https://microsoftedge.microsoft.com/addons/detail/cookieeditor/)，登录 Bilibili 后选择 `Netscape` 格式导出，将剪贴板内容保存为 `cookies.txt`。

将文件放到项目根目录并命名为 `cookies.txt`、`www.bilibili.com_cookies.txt` 或 `bilibili_cookies.txt`，pipeline 会自动检测。也可以显式指定：

```powershell
uv run --no-sync python -m scripts.run_pipeline `
  "<bilibili-url>" `
  --language zh `
  --cookies .\cookies.txt
```

Cookie 文件包含登录凭据，不应提交到版本控制，也不应复制到任务、总结或错误说明中。

## 进一步文档

- [项目架构](references/architecture.md)：prepare/continue/complete 数据流、job 状态和 artifact 边界。
- [错误处理](references/error-handling.md)：setup、下载、Cookie、job 恢复和总结校验故障。

## 主要第三方依赖

- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)：解析 Bilibili 元信息并下载字幕和音频。
- [`ffmpeg-binaries-compat`](https://pypi.org/project/ffmpeg-binaries-compat/)：提供项目使用的 `ffmpeg` 和 `ffprobe`。
- [`uv`](https://docs.astral.sh/uv/)：Python 3.12 环境和依赖管理入口。
