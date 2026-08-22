# 错误处理

仅在 Bilibili 资源准备、job continue 或 summary completion 失败时使用此 reference。转写模型和 cache 失败归独立的 `audio-transcribe` Skill 处理。

## 快速索引

- [Setup 与依赖](#setup-与依赖)
- [下载与网络失败](#下载与网络失败)
- [HTTP 412 与 Cookie](#http-412-与-cookie)
- [字幕选择](#字幕选择)
- [Job Status](#job-status)
- [外部转写输入](#外部转写输入)
- [Summary Completion](#summary-completion)
- [日志](#日志)
- [停止条件](#停止条件)

## Setup 与依赖

- 使用 `.\scripts\setup\setup_windows.bat` 运行 setup。
- 如果 `uv` 不可用，从 <https://docs.astral.sh/uv/> 安装，然后重新运行 setup。
- 如果现有 `.venv` 未使用 Python 3.12，停止执行。不得自动删除或替换它。
- 如果 `.venv` 不完整，仅在用户明确批准后移除或修复它，然后重新运行 setup。
- setup 后的命令使用 `uv run --no-sync python`。
- 依赖同步失败时，检查 setup 日志以及 `pyproject.toml` / `uv.lock`。
- 如果无法 import `audio_transcribe_contract`，重新运行 setup；不得用复制的 Skill 源码替换固定版本的 contract。
- `ffmpeg-binaries-compat` 是受支持的 ffmpeg 来源。如果无法解析 `ffmpeg` 或 `ffprobe`，重新运行 setup，不得依赖系统 PATH。
- 此 setup 不安装 ASR 依赖或模型。job 需要转写时，遵循 `audio-transcribe` 文档。

## 下载与网络失败

- 检查完整日志，定位原始 yt-dlp、网络、超时或文件系统错误。
- 重试前确认 Bilibili URL 可访问且受支持。
- 不得用其他来源的内容替代失败的 Bilibili fetch。
- 没有可用原生字幕或字幕被明确跳过时，必须提供音频。
- job 进入 `preparing` 后发生致命错误，会生成带有简洁 `error.stage/type/message` 的 `failed`，且不存储 traceback 或 Cookie 内容。
- 不得手动把 `failed` 改为其他状态。修复原因后重新运行 preparation，不得覆盖任何有效的现有 job。

## HTTP 412 与 Cookie

- 如果 Bilibili 返回 `HTTP 412`，停止当前运行。不得查询其他来源或生成 summary。
- 要求用户提供 Netscape 格式的 Cookie 文件。经过测试的 Chrome 和 Edge 导出流程见 [README.md](../README.md) 的 Cookie 章节。
- pipeline 自动检测 Skill 根目录中的 `cookies.txt`、`www.bilibili.com_cookies.txt` 和 `bilibili_cookies.txt`。
- 使用其他文件名或位置时：

```powershell
uv run --no-sync python -m scripts.run_pipeline `
  "<bilibili-url>" `
  --language zh `
  --cookies .\cookies.txt
```

- 如果 Cookie 被拒绝，确认其来自已登录的 Bilibili session、使用 Netscape 格式且尚未过期。
- 禁止把 Cookie 值复制到 `summary_job.json`、summary 或错误报告中。

## 字幕选择

- 仅可复用属于所请求 Bilibili language group 且能解析为非空 segment 的 `.srt` 文件。
- start 与 end timestamp 相等的 cue 会被记录并跳过。其他有效 cue 仍可使用；如果没有剩余有效 cue，尝试其他字幕或回退到音频转写。
- 其他字幕文件不会阻止重新尝试下载目标语言字幕。
- 空、格式错误或不可读的 cached SRT 应写入日志，并在可能时替换。
- `--skip-subtitles` 会有意跳过 cached 字幕和下载字幕，并要求存在音频。
- 如果没有有效字幕但存在音频，成功的 preparation 结束于 `needs_transcription`；这不是失败。
- Bilibili `--language` 值不是 ASR language。不得把它传给 `audio-transcribe`。

## Job Status

必须读取并验证输出的 `summary_job.json`；不得根据生成的文件名推断成功。

- `preparing`：preparation 在进入稳定状态前中断。重新运行，或诊断 preparation 日志。
- `needs_transcription`：使用相对于 job 的音频路径调用 `audio-transcribe`，然后使用 manifest 绝对路径调用 `continue_summary`。
- `prompt_ready`：写入或修复预期 summary，然后调用 `complete_summary`。
- `complete`：completion 时 source 和 summary 有效。允许重复运行 completion；不得覆盖 job。
- `failed`：preparation 遇到致命错误。检查其中的简洁错误和完整日志。

拒绝 schema 错误、status 未知、固定顶层 key 缺失、nullability 无效、内部路径为绝对路径或存在相对路径逃逸的 job。不得手动修复 job JSON。

Preparation 禁止静默替换已有的 `prompt_ready` 或 `complete` job。如果新请求解析为已有 BVID，确认精确的结果目录。

## 外部转写输入

仅从 `needs_transcription` 执行 continue：

```powershell
uv run --no-sync python -m scripts.continue_summary `
  "<absolute-summary-job-path>" `
  --transcription-manifest "<absolute-result-manifest-path>"
```

transcription manifest 必须使用绝对路径。固定版本的 `audio-transcribe-contract` 验证其 complete status、schema、受限 artifact 路径、digest、identity、transcript segment 和 raw timestamp。随后，continue 比较 job `resources.audio` 的 SHA-256 与 `manifest.audio.id`。contract 或 audio-identity 失败时，不得发布 `transcript.md`、prompt 或更新后的 job。

continue 失败时：

- prompt 发布尚未成功时，保持 job 为 `needs_transcription`；
- 不得编辑、删除或尝试修复外部 transcription 目录；
- 报告加载或路径安全原因，由用户或 `audio-transcribe` workflow 提供可用结果。

对于已经绑定 transcription 的 job，使用同一 manifest 再次调用 continue 时，会在刷新 prompt 前重新验证外部 manifest、job audio identity 和预期渲染的 Markdown。不同的 manifest 会被拒绝，不得静默替换。只有明确调用 continue 时，才更新已有 ready 或 complete job。

## Summary Completion

写入预期 summary 后：

```powershell
uv run --no-sync python -m scripts.complete_summary "<absolute-summary-job-path>"
```

出现以下情况时，completion 失败且不改变 `prompt_ready`：

- summary 文件缺失或不是有效 UTF-8；
- 仍有 template placeholder 或 prompt 注释；
- 必需结构或语言验证失败。

适用时修复现有 summary，并重复运行 completion。原生字幕 job 仍验证其 transcript source。completion 期间不重新验证外部 transcription artifact。有效 summary 生成 `complete`；重复运行 completion 会成功，且不改写 job。

## 日志

- Setup 日志写入 `.cache/logs/`。
- fetch 和 pipeline 日志从该目录开始，确定结果目录后移动到 `results/<BVID>/`。
- continue 和 complete 命令输出简洁的验证或状态结果。preparation 失败使用 pipeline 日志，后续阶段失败使用命令输出。
- 日志不得记录 transcript 文本、Cookie 内容或外部模型对象。
- `summary_job.error` 仅存储 `stage`、异常类型和 message；完整 traceback 写入日志。
- 报告失败时，应包含精确命令、简洁错误、job 路径和完整日志路径。不得包含 secret。

## 停止条件

出现以下情况时，停止执行，不得生成或完成 summary：

- 请求需要画面分析；
- 输入不是受支持的 Bilibili URL；
- Bilibili 返回 `HTTP 412`，且没有有效 Cookie；
- 既没有可用的原生字幕，也没有可用音频；
- job 为 `preparing`、`needs_transcription` 或 `failed`；
- job 未通过 schema 或路径验证，或无法安全读取必需的内部 artifact；
- 无法安全解析必需的 prompt、template、transcript 或 summary 路径；
- 转写在生成完整公共 manifest 前失败。

禁止通过手动编辑 job status 绕过停止条件。
