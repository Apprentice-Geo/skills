# 错误处理

在选择受限环境的执行权限与数据目录，或诊断依赖检查、setup、Bilibili 资源准备、job continue 与 summary completion 失败时使用此 reference。转写模型和 cache 失败归独立的 `audio-transcribe` Skill 处理。

## 执行权限与沙箱

- 执行权限选择遵循 [SKILL.md 的环境策略](../SKILL.md#环境)。已知沙箱不满足所需权限时，直接使用宿主允许的执行机制，不先在同一限制下反复尝试。宿主不允许所需权限时，报告具体受阻步骤；已有本地输入且权限足够时，可继续本地总结与校验。
- setup 可能下载 Python 3.12，并通过 uv 访问包索引和 GitHub 固定 tag 合同依赖；需要相应网络权限，以及 Skill `.venv`、uv 缓存和 Python 安装位置的写入权限。仅放开 Bilibili 域名不足以完成 setup。
- 首次资源准备需要访问 Bilibili 的页面、接口和字幕服务；字幕不可用、被明确跳过或后来补充音频时，还需访问音频 CDN。浏览器或搜索工具能打开视频页面，不代表命令行下载进程具有相同网络权限。可用字幕路径不下载音频，但仍须读取 job 确认已进入 `prompt_ready`。
- 依赖检查会写入日志与 JSON 报告，并启动 uv、ffmpeg 和 ffprobe 子进程。继续与完成步骤使用本地输入，也会写日志、获取 job lock 或更新文件。网络权限、目录读写权限和子进程执行权限分别判断，不能把这些命令当作无需写权限的纯读取操作。
- DNS、连接、代理或证书错误先结合原始日志与宿主限制判断；文件访问被拒绝先核对绝对失败路径。不把所有网络错误都归因于沙箱，不把网络或权限错误当作缺包，也不通过更换镜像、重装依赖或改用其他内容来源掩盖原始失败。

## 运行目录与写入失败

`scripts/runtime_paths.py` 是本 Skill 的运行数据路径来源。CLI 的 `--data-dir` 优先于 `BILI_AUDIOSUMMARY_DATA_DIR`，未配置时使用 Skill 根目录。运行数据包括 `<data-dir>/.cache/logs/`、`<data-dir>/.cache/uv/` 和 `<data-dir>/results/`；字幕和总结的任务内 artifact 跟随各自 job。日志不随 job 移动或删除。

源码、提示词资源、`.venv`、第三方库及系统临时目录不因数据目录设置而迁移。显式 `UV_CACHE_DIR` 优先；直接使用 `uv run` 时应在启动 uv 前设置它。外部转写 workspace、缓存和模型位置由 `audio-transcribe` 配置，本 Skill 不保证它们跟随数据目录变化。

恢复、补充音频、重新总结或完成任务时，job 必须位于当前配置的 `<data-dir>/results/<BVID[_pN]>/summary_job.json`。即使传入 job 绝对路径，数据目录不匹配也会被拒绝。恢复历史任务应先把 `--data-dir` 或环境变量设回对应目录；切换数据目录不会自动查找或迁移任务。字幕 job 中已保存的绝对 artifact 路径也不会自动改写。

创建目录或打开日志失败时，CLI 向 stderr 输出操作、绝对失败路径、异常类型和原始系统错误，退出码为 `1`；日志未创建时不输出 `Full log`，不继续业务或安装依赖。根据失败路径选择显式可写的数据目录再执行；不能仅凭访问被拒绝就断定是 Windows ACL 或沙箱，也不能把文件系统错误当作缺包并运行 setup。

依赖检查通过但 JSON 报告发布失败时仍返回 `1`，单独报告发布失败；只有已建立的日志才输出 `Full log`，没有成功发布的报告不得输出成功报告路径。检查器关键前置错误仍使用原有退出码，Bilibili CookieRequired 的专用退出码仍只用于 Cookie 错误。

数据目录可写只解决运行数据写入，依赖同步还需要 `.venv` 可写。setup 不为初始化 cache 或日志顺便创建结果目录，也不安装转写模型。

## 快速索引

- [错误处理](#错误处理)
  - [执行权限与沙箱](#执行权限与沙箱)
  - [运行目录与写入失败](#运行目录与写入失败)
  - [快速索引](#快速索引)
  - [Setup 与依赖](#setup-与依赖)
  - [下载与网络失败](#下载与网络失败)
  - [HTTP 412 与 Cookie](#http-412-与-cookie)
  - [字幕选择](#字幕选择)
  - [Job Status](#job-status)
  - [外部转写输入](#外部转写输入)
  - [输入与总结替换失败](#输入与总结替换失败)
  - [Summary Completion](#summary-completion)
  - [日志](#日志)
  - [停止条件](#停止条件)
  - [合同包检查](#合同包检查)

## Setup 与依赖

- 依赖检查、setup 和复查的执行顺序与次数统一遵循 [SKILL.md 的环境策略](../SKILL.md#环境)。
- `check_dependencies.bat` 在 Windows、项目文件、uv 或 `.venv\Scripts\python.exe` 前置检查失败时可能直接返回 `2`，不产生 Python 日志或 JSON 报告。先核对这些前置项；只有缺少环境或确认依赖问题才进入 setup，不虚构日志路径。
- 必需依赖缺失、已安装包不一致、合同包版本不符或公共 API 不完整适用依赖同步。一次任务只执行一次 setup 和复查；仍失败时报告具体问题。
- 日志/报告写入失败、数据或 `.venv` 权限错误不进入 setup，按真实路径诊断。项目文件缺失、合同声明不可读或无效先修复项目；工具不可用、Python 版本错误或子进程无法启动先诊断工具与解释器，不当作缺包处理。
- Bilibili 网络、Cookie、输入文件和 job/artifact 错误按各自章节恢复，不反复 setup。此 Skill 不配置 ASR Provider、不安装模型；外部转写遵循 `audio-transcribe` 自身修复策略。
- 如果 `uv` 不可用，从 <https://docs.astral.sh/uv/> 安装，再返回环境策略规定的后续步骤。
- 如果现有 `.venv` 未使用 Python 3.12，停止执行。不得自动删除或替换它。
- 如果 `.venv` 不完整，仅在用户明确批准后移除或修复它。
- setup 后的命令使用 `uv run --no-sync python`。
- 依赖同步失败时，检查 setup 日志以及 `pyproject.toml` / `uv.lock`。
- 如果无法 import `audio_transcribe_contract`，检查 setup 日志、GitHub 网络访问，以及项目声明的 Git tag 合同依赖是否已同步；不得用复制的 Skill 源码替换它。
- `ffmpeg-binaries-compat` 是受支持的 ffmpeg 来源。如果无法解析 `ffmpeg` 或 `ffprobe`，检查 setup 日志和依赖检查报告；不得依赖系统 PATH。
- 此 setup 不安装 ASR 依赖或模型。job 需要转写时，遵循 `audio-transcribe` 文档。

## 下载与网络失败

- 检查完整日志，定位原始 yt-dlp、网络、超时或文件系统错误。
- 重试前确认 Bilibili URL 可访问且受支持。
- pipeline 在联网下载前就要求 URL 能解析出 `/video/BV.../`。`b23.tv` 短链接或旧式 av 地址应先解析到实际 BV 视频地址，保留所需的 `p` 参数；不能解析时请求完整视频地址，不把 URL 校验错误当作网络失败。当前 pipeline 不批量处理合集或全部分 P。
- 不得用其他来源的内容替代失败的 Bilibili fetch。
- 没有可用原生字幕或字幕被明确跳过时，必须提供音频。
- 字幕可用时不下载备用音频；已有字幕任务后来需要音频时，按[补充音频并更换转写](OPERATIONS.md#补充音频并更换转写)使用 `scripts.prepare_audio`。准备失败不会撤销现有总结；修复原因后重试，不删除任务。已登记音频缺失时明确报错，不自动重下载后替换其身份。
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
- 倒置或重叠的时间戳使该字幕不可用，尝试其他字幕或回退到音频转写。字幕文件权限错误直接失败，不当作无字幕回退；HTTP 412 仍遵循 Cookie 规则。
- 其他字幕文件不会阻止重新尝试下载目标语言字幕。
- 空或格式错误的 cached SRT 应写入日志，并在可能时替换。
- 下载结果只有非 SRT 字幕时记录 warning；没有可用 SRT 时由 pipeline 准备音频。
- `--skip-subtitles` 会有意跳过 cached 字幕和下载字幕，并要求存在音频。
- 如果没有有效字幕但存在音频，成功的 preparation 结束于 `needs_transcription`；这不是失败。
- Bilibili `--language` 值不是 ASR language。不得把它传给 `audio-transcribe`。

## Job Status

必须读取并验证输出的 `summary_job.json`；不得根据生成的文件名推断成功。

- `preparing`：preparation 在进入稳定状态前中断。重新运行，或诊断 preparation 日志。
- `needs_transcription`：使用相对于 job 的音频路径调用 `audio-transcribe`，然后使用 manifest 绝对路径调用 `continue_summary`。
- `prompt_ready`：写入或修复预期 summary，然后调用 `complete_summary`。
- `complete`：已有总结通过了 completion；核对是否满足本次请求，满足时可重复校验后交付，需要重写时使用 `continue_summary --regenerate-summary`。不得手动覆盖 job。
- `failed`：preparation 遇到致命错误。检查其中的简洁错误和完整日志。

拒绝 schema 错误、status 未知、固定顶层 key 缺失、nullability 无效、内部路径为绝对路径或存在相对路径逃逸的 job。不得手动修复 job JSON。

Preparation 复用已有稳定任务，不重复下载。已有字幕任务会拒绝冲突的 `--language` 或 `--skip-subtitles`；如需音频输入，先用 `scripts.prepare_audio` 准备 `resources.audio`，转写后显式导入 manifest。只更换总结语言使用 `--summary-language`。

本地 job 只接受[当前结构](ARCHITECTURE.md#任务与恢复)。包含旧 `schema_version` 或其他不兼容字段时停止，保留下载资源、转写和用户总结；不手动删字段绕过验证，不自动迁移或重建。

## 外部转写输入

显式传入 manifest 可导入或替换转写：

```powershell
uv run --no-sync python -m scripts.continue_summary "<absolute-summary-job-path>" `
  --transcription-manifest "<absolute-manifest-path>"
```

adapter 校验公共 bundle，并比较 job 音频的 SHA-256。`resources.audio` 为 `null` 时先用 `scripts.prepare_audio` 补充音频。读取或音频身份失败时保留当前输入与总结；不得编辑、删除或修复上游目录。通过验证后，旧总结改名留存，任务回到 `prompt_ready`。同一路径再次传入也表示显式重新导入。

不传 manifest 时使用本地输入。单纯恢复可重建 prompt 和原生字幕 Markdown，不改写已有总结；外部转写的本地 Markdown 缺失时，需要重新提供 manifest。切语言使用 `--summary-language`，支持范围见 [SKILL.md](../SKILL.md#1-准备资源和任务)；同语言按新要求重写使用 `--regenerate-summary`。两者均无需重新下载或转写；没有转写输入时不能要求重新总结。

旧任务使用集合外的总结语言时，继续命令会在改写产物前拒绝。显式指定受支持的 `--summary-language` 后继续，旧总结按语言切换规则归档；不手动编辑 job 或删除已有总结。

## 输入与总结替换失败

输入替换、实际语言切换和 `--regenerate-summary` 均先撤销 `complete`，再将旧总结与目标同名总结归档为 `.superseded-<id>.md`；归档内容不能作为当前结果交付。

- 撤销完成状态失败：不移动旧总结，也不替换输入。
- 归档失败：回滚已经移动的总结。若回滚也失败，按错误报告中的备份路径保留并找回内容；不要删除任务。
- 输入、prompt 或最终 job 发布失败：多个 artifact 不构成整体事务，旧输入不保证原样保留；任务可能已回到 `prompt_ready`，不能仅凭状态交付。

按原操作恢复：替换转写时重新传入目标 manifest；切换语言时再次传入目标 `--summary-language`；使用当前转写重写时再次传入 `--regenerate-summary`。恢复后重新编写或检查总结，并通过 completion 再交付。仅重写或切换语言时，不为恢复而重新导入无关的上游转写。

## Summary Completion

写入预期 summary 后：

```powershell
uv run --no-sync python -m scripts.complete_summary "<absolute-summary-job-path>"
```

出现以下情况时，completion 失败且不改变 `prompt_ready`：

- summary 文件缺失、为空或不是有效 UTF-8；
- 适用的 transcript source 无效。

脚本不检查正文语言、固定章节或格式；总结语言是否符合任务、内容是否回应用户请求、有 transcript 支持、时间戳对应相关内容，以及是否误复制 prompt，均由 Agent 在 completion 前检查。适用时修复现有 summary，并重复运行 completion。原生字幕 job 仍验证其 transcript source。completion 期间不重新验证外部 transcription artifact。通过上述脚本校验的 summary 生成 `complete`；重复运行 completion 会成功，且不改写 job。

## 日志

- 所有命令日志固定写入数据目录 `.cache/logs/`，不移动到 job；删除任务不会删除日志。
- Python 日志会话启动后，成功结果和进度写入 stdout，warning 和 error 写入 stderr；相同消息正文也写入文件。普通 logger、第三方 logger、Python warnings、逐项 PASS 明细和完整 traceback 只写入文件。
- 子进程的完整命令和输出只写入日志，失败由顶层命令输出一条简洁错误及精确 `Full log` 路径，避免重复回放大量输出。
- argparse 的 help 与参数解析错误、`.bat` 启动检查以及原始 `uv python install 3.12` 输出位于 Python 日志会话之外。该 uv 命令在 bootstrap 前失败时不会有 setup 日志，也不得推断 `Full log` 路径。
- 日志不得记录 transcript 文本、Cookie 内容或外部模型对象。
- `summary_job.error` 仅存储 `stage`、异常类型和 message；完整 traceback 写入日志。
- 报告失败时，应包含精确命令、简洁错误、job 路径和完整日志路径。不得包含 secret。

## 停止条件

以下情况禁止生成、完成或交付 summary；修复原因后可继续任务：

- 事前已知请求所需的关键信息主要依赖画面，或读取 transcript 后确认其整体不足以支持请求；
- 输入不是受支持的 Bilibili URL；
- Bilibili 返回 `HTTP 412`，且没有有效 Cookie；
- 既没有可用的原生字幕，也没有可用音频；
- job 未通过 schema 或路径验证，或无法安全读取必需的内部 artifact；
- 无法安全解析必需的 prompt、transcript 或 summary 路径；
- 转写在生成完整公共 manifest 前失败。

job 为 `needs_transcription` 时，继续调用转写 Skill 并导入结果；`preparing` 或 `failed` 时诊断并恢复资源准备。这些状态表示总结输入尚未就绪，按 [Job Status](#job-status) 推进，不终止整个任务。禁止通过手动编辑 job status 绕过校验。


## 合同包检查

检查器和 setup 复用本 Skill 的 `scripts/contract_check.py`：从 `pyproject.toml` 读取合同包要求，通过当前解释器的 `importlib.metadata` 读取 distribution 版本，检查 `load_result`、`TranscriptionResult`、`ResultValidationError` 和 `PUBLIC_SCHEMA_VERSION=4`。版本不符、包缺失、声明不可读或无效、API 不完整时返回失败，不能报告 ready。JSON 检查项包含 expected、actual 和同步建议。

缺少 `packaging` 时，检查器仍输出日志与 JSON 报告；`contract:version` 标记失败并给出依赖同步建议，公共 API 和其他检查继续执行。

`uv pip check` 仅检查已安装包之间的一致性，不证明它们满足当前项目声明。按本 Skill 环境策略显式同步后，使用 `uv run --no-sync python -m scripts.check_dependencies` 复查，避免自动同步掩盖旧包问题。实际接入转写时仍通过 `load_result()` 验证完整公共 bundle；能力检查不能代替该验证。
