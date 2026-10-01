# 错误处理

仅在 Bilibili 资源准备、job continue 或 summary completion 失败时使用此 reference。转写模型和 cache 失败归独立的 `audio-transcribe` Skill 处理。

## 运行目录与写入失败

`scripts/runtime_paths.py` 是本 Skill 的运行数据路径来源。CLI 的 `--data-dir` 优先于 `BILI_AUDIOSUMMARY_DATA_DIR`，未配置时使用 Skill 根目录。布局保留 `<data-dir>/.cache/logs/`、`<data-dir>/.cache/uv/` 和 `<data-dir>/results/`；转写 workspace 跟随结果目录，字幕和总结的任务内 artifact 跟随各自 job。活动日志确定可信 job 后仍可移入 job，删除日志保留在 cache 中。

所有命令启动时解析绝对路径，再传给业务入口。源码、模板、`.venv`、已安装模型、第三方库及系统临时目录不因数据目录设置而迁移。显式 `UV_CACHE_DIR` 优先；直接使用 `uv run` 时应在启动 uv 前设置它。历史任务在原目录继续可用；切换根目录不自动查找或迁移其他目录的任务，字幕 job 已保存的绝对 artifact 路径语义不变。

创建目录或打开日志失败时，最外层 CLI 直接向 stderr 输出操作、绝对失败路径、异常类型和原始系统错误，退出码为 `1`；明确日志未创建，不输出 `Full log`，不继续业务或安装依赖，不回退到未知目录。日志初始化失败恢复 logger、warnings hook 和 handler 状态。根据失败路径选择显式可写的数据目录再执行；不能仅凭访问被拒绝就断定是 Windows ACL 或沙箱，也不能把文件系统错误当作缺包并运行 setup。

依赖检查通过但 JSON 报告发布失败时仍返回 `1`，单独报告发布失败；只有已建立的日志才输出 `Full log`，没有成功发布的报告不得输出成功报告路径。日志移动失败时重新打开原日志并继续；原日志或移动后的日志无法重新打开时停止，报告真实路径和系统错误，不声称活动日志可用。检查器关键前置错误仍使用原有退出码，Bilibili CookieRequired 的专用退出码仍只用于 Cookie 错误。

数据目录可写只解决运行数据写入。依赖同步需要 `.venv` 可写，模型安装需要模型目录可写；应分别根据真实失败路径诊断。setup 不为初始化 cache 或日志顺便创建模型或结果目录。

## 快速索引

- [错误处理](#错误处理)
  - [快速索引](#快速索引)
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

- 依赖检查、setup 和复查的执行顺序与次数统一遵循 [SKILL.md 的环境策略](../SKILL.md#环境)。
- 必需依赖缺失、已安装包不一致、合同包版本不符或公共 API 不完整适用依赖同步。每轮复查重新分类，只有修复仍适用且符合授权规则才继续下一轮，最多三轮。
- 日志/报告写入失败、数据或 `.venv` 权限错误不进入 setup，按真实路径诊断。项目文件缺失、合同声明不可读或无效先修复项目；工具不可用、Python 版本错误或子进程无法启动先诊断工具与解释器，不当作缺包处理。
- Bilibili 网络、Cookie、输入文件和 job/artifact 错误按各自章节恢复，不反复 setup。此 Skill 不配置 ASR Provider、不安装模型；外部转写遵循 `audio-transcribe` 自身修复策略。
- 如果 `uv` 不可用，从 <https://docs.astral.sh/uv/> 安装，再返回环境策略规定的后续步骤。
- 如果现有 `.venv` 未使用 Python 3.12，停止执行。不得自动删除或替换它。
- 如果 `.venv` 不完整，仅在用户明确批准后移除或修复它。
- setup 后的命令使用 `uv run --no-sync python`。
- 依赖同步失败时，检查 setup 日志以及 `pyproject.toml` / `uv.lock`。
- 如果无法 import `audio_transcribe_contract`，检查 setup 日志以及固定版本的 contract 是否已同步；不得用复制的 Skill 源码替换它。
- `ffmpeg-binaries-compat` 是受支持的 ffmpeg 来源。如果无法解析 `ffmpeg` 或 `ffprobe`，检查 setup 日志和依赖检查报告；不得依赖系统 PATH。
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
- 要求用户提供 Netscape 格式的 Cookie 文件。Chrome 和 Edge 的导出方式见 [README.md](../README.md#cookies-导出)。
- pipeline 自动检测 Skill 根目录中的 `cookies.txt`、`www.bilibili.com_cookies.txt` 和 `bilibili_cookies.txt`。
- 使用其他文件名或位置时：

```powershell
uv run --no-sync python -m scripts.run_pipeline `
  "<bilibili-url>" `
  --language zh `
  --cookies .\cookies.txt
```

- 如果 Cookie 被拒绝，确认其来自已登录的 Bilibili session、使用 Netscape 格式且尚未过期。
- Cookie 文件只传给 yt-dlp，用于请求 Bilibili 视频元数据、字幕和音频。禁止把 Cookie 值复制到 `summary_job.json`、summary、日志或错误报告中。

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
  --transcription-manifest "<absolute-manifest-path>"
```

transcription manifest 必须使用绝对路径。adapter 通过固定版本的公共 contract 读取转写输入，随后 continue 比较 job `resources.audio` 的 SHA-256 与输入音频身份。读取或 audio-identity 失败时，不得发布 `transcript.md`、prompt 或更新后的 job。

continue 失败时：

- prompt 发布尚未成功时，保持 job 为 `needs_transcription`；
- 不得编辑、删除或尝试修复外部 transcription 目录；
- 报告加载或路径安全原因，由用户或 `audio-transcribe` workflow 提供可用结果。

对于已经导入 transcription 的 job，再次调用 continue 会直接复用本地快照，不读取传入的 manifest。需要采用不同或重新发布的 transcription manifest 时，先确认没有命令正在操作同一 job，再运行：

```powershell
uv run --no-sync python -m scripts.remove_summary_job "<absolute-summary-job-path>"
```

此命令删除整个 job 目录，包括下载资源、transcript、prompt 和 summary；需要保留时先要求用户自行备份。随后重新运行 preparation 和 continue。删除 summary job 不会删除或强制重新生成 `audio-transcribe` 结果。不得手动删除部分 artifact 或清空整个 `results/`。

## Summary Completion

写入预期 summary 后：

```powershell
uv run --no-sync python -m scripts.complete_summary "<absolute-summary-job-path>"
```

出现以下情况时，completion 失败且不改变 `prompt_ready`：

- summary 文件缺失或不是有效 UTF-8；
- 仍有 template placeholder 或 prompt 注释；
- 适用的 transcript source 无效。

summary 语言比例不足只产生 warning，命令仍以成功状态退出并把 job 改为 `complete`。warning 写入 stderr 和当次 complete 日志，不写入 `summary_job.json`。读取 warning，并根据总结指令判断是否需要修订。

脚本不检查必需 section 是否存在，也不判断内容是否有 transcript 支持或时间戳是否对应相关内容；这些项目由 Agent 在运行 completion 前检查。适用时修复现有 summary，并重复运行 completion。原生字幕 job 仍验证其 transcript source。completion 期间不重新验证外部 transcription artifact。通过上述脚本校验的 summary 生成 `complete`；重复运行 completion 会成功，且不改写 job。

## 日志

- Setup、依赖检查、独立 `validate_summary` 和 `remove_summary_job` 日志写入 `.cache/logs/`。remove 日志不会随 job 目录删除。
- fetch 和 pipeline 日志从 `.cache/logs/` 开始，确定结果目录后移动到 `results/<BVID>/`；保留已有移动机制。
- continue 和 complete 日志从 `.cache/logs/` 开始；命令成功后移动到对应 job 目录，失败时留在 cache。
- Python 日志会话启动后，成功结果和进度写入 stdout，warning 和 error 写入 stderr；相同消息正文也写入文件。普通 logger、第三方 logger、Python warnings、逐项 PASS 明细和完整 traceback 只写入文件。
- 子进程的完整命令和输出只写入日志，失败由顶层命令输出一条简洁错误及精确 `Full log` 路径，避免重复回放大量输出。
- argparse 的 help 与参数解析错误、`.bat` 启动检查以及原始 `uv python install 3.12` 输出位于 Python 日志会话之外。该 uv 命令在 bootstrap 前失败时不会有 setup 日志，也不得推断 `Full log` 路径。
- 日志不得记录 transcript 文本、Cookie 内容或外部模型对象。
- `summary_job.error` 仅存储 `stage`、异常类型和 message；完整 traceback 写入日志。
- 报告失败时，应包含精确命令、简洁错误、job 路径和完整日志路径。不得包含 secret。

## 停止条件

出现以下情况时，停止执行，不得生成或完成 summary：

- 事前已知请求所需的关键信息主要依赖画面，或读取 transcript 后确认其整体不足以支持请求；
- 输入不是受支持的 Bilibili URL；
- Bilibili 返回 `HTTP 412`，且没有有效 Cookie；
- 既没有可用的原生字幕，也没有可用音频；
- job 为 `preparing`、`needs_transcription` 或 `failed`；
- job 未通过 schema 或路径验证，或无法安全读取必需的内部 artifact；
- 无法安全解析必需的 prompt、template、transcript 或 summary 路径；
- 转写在生成完整公共 manifest 前失败。

禁止通过手动编辑 job status 绕过停止条件。


## 合同包检查

检查器和 setup 复用本 Skill 的 `scripts/contract_check.py`：从 `pyproject.toml` 读取合同包要求，通过当前解释器的 `importlib.metadata` 读取 distribution 版本，检查 `load_result`、`TranscriptionResult`、`ResultValidationError` 和 `PUBLIC_SCHEMA_VERSION=3`。版本不符、包缺失、声明不可读或无效、API 不完整时返回失败，不能报告 ready。JSON 检查项包含 expected、actual 和同步建议。

`uv pip check` 仅检查已安装包之间的一致性，不证明它们满足当前项目声明。按本 Skill 环境策略显式同步后，使用 `uv run --no-sync python -m scripts.check_dependencies` 复查，避免自动同步掩盖旧包问题。实际接入转写时仍通过 `load_result()` 验证完整公共 bundle；能力检查不能代替该验证。
