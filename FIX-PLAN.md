# 转写、字幕与 Bilibili 总结 Skill 修复计划

## 范围与当前状态

- 涉及 `audio-transcribe`、`subtitle-creator`、`bili-audiosummary`。
- 本文是待实施计划，不代表代码已经修复。依据是当前仓库实现、字幕环境包元数据，以及用户提供的调用记录和复现结果。
- 本轮只检查代码与文档，不安装依赖、不运行真实转写、不访问 Bilibili、不删除或迁移现有任务。
- 优先修复 Qwen 连续导入与合同包版本漏检，再解决运行数据目录和日志启动错误，最后同步修复流程及字幕输出契约。
- 保留无关本地修改，仅修改本计划涉及的三个 Skill。

## 已确认的问题与证据边界

| Skill | 当前实现 | 修复方向 |
| --- | --- | --- |
| audio-transcribe | 安装器连续导入 SpeechBrain、Qwen；检查器逐个使用独立进程导入；运行时另有可选别名处理 | 统一导入兼容处理，增加组合导入检查，保留异常归因 |
| subtitle-creator | 声明合同包 `0.3.0`，环境元数据为 `0.1.1`；检查器仅确认可导入 | 验证安装版本符合项目声明，检查当前合同 API/schema |
| 三个 Skill | 日志、报告、结果或内部缓存存在固定写入 Skill 目录的路径 | 统一各自的数据目录解析，允许配置可写位置 |
| subtitle-creator、bili-audiosummary | 日志启动发生在业务异常处理外 | 捕获启动失败，输出真实路径和系统错误，避免裸 traceback |
| audio-transcribe | 默认 setup 固定 CPU，环境修复和指定 Provider 的停止规则冲突 | 根据失败原因及请求 Provider 修复一次，条件安装模型 |
| subtitle-creator | 创建命令只输出 job 路径 | 输出现有状态与可用 artifact，便于直接恢复 |
| bili-audiosummary | 合同包声明同为 `0.3.0`，检查器只做 import 与 `uv pip check` | 同步防止合同包版本漏检；不声称已复现其安装版本异常 |

Qwen 的连续导入故障和字幕检查器返回 `ready` 的复现由用户提供，本轮没有重新加载这些模型依赖。字幕环境 `0.1.1` 的元数据已直接读取确认。历史权限错误的具体来源尚不能确定，不能仅凭代码归因为 Windows ACL 或 Agent 沙箱。

用户：复现你可以自己在本机codex的会话记录里面找，找不到对话可以跳过对应的无法复现问题，不强行修复难以复现的问题。此外，仓库中可能有残留的异常权限目录，主要是一些测试使用的临时目录，可以删除它们并创建权限正常的目录。

### 日志权限问题的含义

能读取 Skill 源码、运行 Python，并不保证能在 Skill 目录写文件。字幕和 Bilibili workflow 先创建 `.cache/logs/` 并打开日志，随后才执行依赖检查或业务处理；目录创建或文件打开被拒绝时，业务尚未开始。

`with LoggingSession(...)` 的进入动作会启动日志。如果 `try` 写在 `with` 内部，就无法捕获进入动作的异常。字幕入口中直接调用 `.start()` 后才进入 `try`，存在同样的问题。

目前初始化日志还会先修改 logger 状态，之后才打开文件；文件打开失败时可能留下部分初始化状态。修复需要同时处理简洁报错和状态恢复。

访问被拒绝可能来自 Windows 文件权限、Agent 写入范围或其他访问限制。实施时保留具体失败路径、异常类型、系统错误编号和消息。它属于文件系统问题，不能据此启动依赖安装；日志未创建时不得输出声称有效的 `Full log` 路径。

## 第一阶段：导入策略与合同包检查

### 1. audio-transcribe：Qwen 连续导入

相关实现：[安装器](audio-transcribe/scripts/setup/install_model.py)、[依赖清单](audio-transcribe/scripts/dependency_policy.py)、[检查器](audio-transcribe/scripts/check_dependencies.py)、[运行时](audio-transcribe/scripts/transcribe.py)。

- 将现有 SpeechBrain 可选别名处理提取到轻量模块，供安装器、检查器、运行时复用。模块本身不得因顶层导入加载模型或整个转写 pipeline。
- 明确同一进程中的语言识别依赖导入、已知可选别名处理和 Qwen 导入顺序。仅处理当前支持的 SpeechBrain 兼容问题，不能吞掉任意导入异常，不能把安装 `k2` 当作通用修复。
- 保留单模块探测，并增加同一进程的组合导入探测；组合探测结果必须参与 Qwen Provider readiness。
- 检查安装器、运行时语言检测和 Qwen 加载入口，确保显式语言路径与自动语言检测路径均使用适用的兼容规则。
- 探测输出记录正在导入的模块、异常类型、缺失模块名（如有）和原始消息。完整 traceback 写入已建立的日志。
- 删除“任何导入失败都代表依赖缺失”的归因；区分必需依赖缺失、可选模块误触发或兼容异常、CUDA build 错误、GPU runtime 不可用。子进程无法启动也不能归因为 Python 缺包。

验收：

- 在现有 Qwen 环境中执行不加载模型的真实连续导入，证明兼容处理有效；单独导入成功不能代替该证据。
- 必需包缺失时明确指出包与模块；兼容异常保留原始原因；缺包、导入冲突和 CUDA 错误分别输出正确诊断。
- 组合导入失败时，即使所有独立导入成功，Qwen 仍不能报告 ready。
- 保持 Whisper readiness 和已有 OpenCC 共享清单逻辑。

### 2. subtitle-creator 与 bili-audiosummary：合同包匹配

相关实现：[字幕检查器](subtitle-creator/scripts/check_dependencies.py)、[字幕声明](subtitle-creator/pyproject.toml)、[Bilibili 检查器](bili-audiosummary/scripts/check_dependencies.py)、[Bilibili 声明](bili-audiosummary/pyproject.toml)。

- 从各自 `pyproject.toml` 的依赖声明读取合同包要求，使用当前检查器解释器的 `importlib.metadata` 读取 distribution 版本。不得依赖包的 `__version__`，也不得在检查器另行硬编码 `0.3.0`。
- 版本不符合声明、包缺失、声明不可读或无效时产生明确失败检查项，记录 expected、actual 和修复方式；不得返回 ready。
- 增加当前公共合同最小能力检查：消费方所需 `load_result`、结果类型、异常类型和公共 schema 标识。以公开包导出为边界，不复制上游完整校验实现。
- `uv pip check` 继续用于已安装包之间的一致性检查，修正其“已经验证当前项目声明”的误导性说明。
- setup 同步后的合同验证与检查器复用相同规则。两个 Skill 各自可独立安装，不互相 import 私有脚本；此阶段不引入通用共享包。
- 实际导入转写结果继续调用 `load_result()` 验证完整 bundle，能力检查不得替代 bundle 验证。

实施顺序：先用测试及尚未同步的字幕环境证明旧包被拒绝，再显式同步字幕环境并复查。Bilibili 环境先检查实际安装版本，只在需要时同步；不假设两个环境现状相同。前后检查避免由 `uv run` 自动同步掩盖旧环境故障。

验收：旧 `0.1.1`、包缺失、版本符合但 API 不完整均为 not_ready；符合项目声明且能力可用时通过。完整公共 bundle 的正反例仍由合同验证和现有 adapter 测试保护。

## 第二阶段：数据目录与日志失败边界

### 3. 各 Skill 的数据目录契约

采用统一的使用方式，但由每个 Skill 在内部集中解析，不跨 Skill import 私有源码。

- 增加 `--data-dir` 和各自环境变量：`AUDIO_TRANSCRIBE_DATA_DIR`、`SUBTITLE_CREATOR_DATA_DIR`、`BILI_AUDIOSUMMARY_DATA_DIR`。
- 优先级为显式 CLI 参数、对应环境变量、Skill 根目录。所有配置在启动时解析成绝对路径，再通过运行上下文传递，避免不同模块固化不同的 import-time 默认值。
- 布局保留 `<data-dir>/.cache/logs/`、`<data-dir>/.cache/uv/`、`<data-dir>/results/`；转写 workspace 跟随结果目录。默认布局不变，已有任务仍可定位。
- 数据根目录仅覆盖本次范围内的日志、报告、结果和内部缓存。源码、模板、`.venv` 和已安装模型路径保持独立；不自动迁移模型或历史任务。
- `.bat` 启动器、setup、检查器和 workflow 都使用相同数据根目录。保留显式 `UV_CACHE_DIR` 的优先级，避免 launcher 先将它固定为 Skill 内目录。
- 本次不将所有第三方库和系统临时目录强制重定位；若发现实际流程还有 Skill 内部固定写入点，记录并纳入路径集中管理。
- `audio-transcribe` CLI 开放 `--results-dir` 并传给已有 `run_transcribe(results_dir=...)`；显式参数覆盖数据根目录下的默认结果目录，日志和报告仍遵循数据目录规则。
- 调用跨多个命令时，持续使用相同环境变量或重复传入相同参数；切换目录不自动搜索、搬迁或合并其他目录中的任务。
- 数据目录可写不代表环境可修复：依赖同步还需要 `.venv` 可写，模型安装还需要模型目录可写，分别诊断。

### 4. 各 Skill 的具体接入范围

**audio-transcribe**

- 检查 `config.py`、`transcribe.py`、`check_dependencies.py`、`setup/environment.py`、`.bat` 启动器及日志会话的路径与启动边界。
- 结果目录变更不能进入内容身份 digest；同一有效请求移动存储位置，不应改变 transcript 字节或身份。
- setup 不应为日志或缓存初始化顺便创建无关的模型、结果目录；在对应操作需要时创建并诊断。

**subtitle-creator**

- 覆盖 create、attach、finalize、remove、check、setup；修改 `subtitle_job.py` 中与固定 `RESULTS_DIR` 绑定的校验。
- 创建、校验和删除必须使用同一解析后的结果根目录，不能出现外部目录创建成功、后续却按默认目录拒绝的情况。
- 删除仍仅接受当前结果根目录下 `<audio-sha256>/subtitle_job.json` 的绝对路径，保留 symlink、junction 和越界防护。修改错误说明中的“默认目录”为“当前配置目录”。
- 默认目录中的历史任务继续可用；切换数据目录不迁移保存绝对 artifact 路径的现有 job，不在原 schema 下改变字段语义。

**bili-audiosummary**

- 覆盖 `run_pipeline`、`continue_summary`、`complete_summary`、`remove_summary_job`、`fetch_audio`、`subtitle_transcript`、`validate_summary`、依赖检查和 setup。
- `run_pipeline` 当前 job 根目录固定为 `RESULTS_DIR`；底层 fetch 已有 `--output-dir`，但它不会移动 pipeline 的 job 根目录，也不会移动启动日志。不能仅让 Agent 改传 fetch 参数来替代此修复。
- pipeline 的 job 根目录、下载资源目录及恢复和删除边界统一使用当前数据目录；保留直接 fetch 的既有输出目录选项，并明确其只控制下载产物。
- 删除只允许当前 `results/<BVID[_pN]>/summary_job.json` 的单个合法任务，保留路径及链接防护。job 内资源仍使用现有相对路径规则。
- 保留原生字幕优先、本地 transcript snapshot、外部转写只读和 Cookie 不入日志的约束。

### 5. 日志和报告无法写入时的行为

- 各 CLI 在最外层捕获日志启动的文件系统错误；日志还不可用时使用直接 stderr 输出，包含操作、绝对失败路径和原始系统错误，返回非零退出码，不输出裸 traceback。
- 此时明确日志未创建，不声称 `Full log` 可用，不继续业务或安装依赖。
- 日志初始化具有失败回滚：恢复原 logger 状态，关闭已创建 handler，不留下活动会话或句柄。成功建立后继续保留原有日志筛选行为。
- 检查报告写入失败单独报告。不能因为依赖检查已通过，就在 JSON 发布失败时返回成功；只有实际存在的日志或报告才输出其路径。
- 已有“日志移入可信 job；移动失败继续使用原日志”的行为保留，并处理移动后重新打开文件失败等路径。删除日志保留在 job 外。
- 不默默回退到另一个未知目录；用户或 Agent 显式指定可写目录后再运行。
- 保留各 CLI 既有退出码语义，例如 Bilibili CookieRequired 的专用退出码、checker 的关键前置错误分类；文件系统错误不得复用 Cookie 错误含义。

验收：

- 通过模拟 `mkdir`、文件打开和报告发布的 `PermissionError`，稳定验证错误路径；无需真实修改 Windows ACL。
- Skill 目录不可写但已安装环境可运行、数据目录可写时，检查和 workflow 的模拟流程成功，且不写 Skill 内日志、结果或内部缓存。
- 数据目录不可写时输出路径与系统错误、非零退出，不安装依赖、不发布 job、不留下 logger 状态。
- 外部结果目录创建、恢复、finalize/complete、删除均有效；越界、链接、junction 删除仍被拒绝，不影响其他 job。
- 日志移动失败、报告发布失败各有针对性验证，stderr 和路径输出符合真实文件状态。

## 第三阶段：修复流程与字幕输出

### 6. 环境修复流程

- `audio-transcribe` 修改旧规则，统一为“检查原因 → 按所请求 Provider 修复环境 → 复查 → 仅安装缺失或无效模型 → 复查 → 转写”。适用的修复最多一次，不重复 setup、不静默切换 Provider。
- setup 增加明确的环境选择并贯通 `.bat` 与 Python bootstrap，默认 CPU 保留；Qwen 请求使用互斥 `qwen3-asr` extra，不先同步 CPU，不使用 `--all-extras`。
- 区分“停止转写”和“允许环境修复”：请求 Provider 未就绪不能转写，已有环境配置授权时可进行适用修复；权限、项目缺失、GPU/驱动问题不会自动进入依赖重装。
- 模型检查复用安装文件和 revision 标记校验；已有 `download_model()` 会跳过有效模型，不新增重复判定或声称它目前每次都会下载。
- `subtitle-creator` 同样依据失败原因决定是否 setup；合同包不匹配进入显式同步，日志权限或文件缺失进入对应诊断。
- `bili-audiosummary` 增加失败分类，避免把日志权限错误送入反复 setup。现有至多三轮 setup 的个人策略先保留，不为了三个 Skill 形式统一而改成一次；只有修复适用且符合其授权规则时才继续下一轮。
- Bilibili 不配置 ASR Provider，不安装模型；外部转写继续遵循 `audio-transcribe` 自身规则。

### 7. subtitle-creator 创建命令输出

- `create_subtitle` 成功时 stdout 输出单行 JSON，由已验证的 job 生成，字段为 `status`、`subtitle_job`、`audio_path`、`normalized_transcript`、`subtitle`。
- 使用现有 `needs_transcription` 和 `editable`，不新增状态、不新增持久化 schema version。
- 所有路径为绝对路径；尚不可用的 artifact 输出 `null`。
- 创建/恢复允许合法编辑和陈旧派生产物，不能将“存在 SRT”当作可交付；只有 SRT 与当前合法 normalized transcript 一致时才输出字幕路径，否则为 `null` 并继续允许 finalize 恢复。
- 复用现有 SRT 有效性规则，避免第二套校验实现。输出摘要不修改 job、不隐式执行 finalize。
- 失败时 stdout 为空，stderr 遵循修复后的日志规则。其他字幕命令的结果行保持现有契约。
- 本次不扩大为 Bilibili 输出格式重构，保留其现有 Summary Job 输出。

验收：新任务、已有 editable、有效 SRT、文本修改造成 SRT 过期、SRT 缺失或损坏均输出正确 JSON；非法时间轴和 baseline 损坏仍失败。Agent 根据输出直接选择转写或编辑/finalize，无需再读取 job 判断状态。

## 文档同步

- 各 Skill 的 `SKILL.md` 更新实际 CLI、配置优先级、检查和修复条件；修改冲突旧规则，不通过追加新规则覆盖。
- `references/ERROR-HANDLING.md` 作为日志启动、权限、报告发布和修复分类的详细来源；`references/ARCHITECTURE.md` 更新运行目录与控制流边界。
- `README.md` 仅说明可配置运行数据位置、隐私和使用影响，并引用详细文档，不重复执行流程。
- 字幕文档删除“create 仅返回 job 路径”和“必须先读取 job 才能判断 status”的旧说明，加入 JSON 示例和 `null` 含义。
- 删除文档中固定“默认 results 才允许删除”的限制描述，改为当前配置根目录下的既有严格删除规则。
- 不修改公共合同字段或 schema，不为可再生诊断数据增加版本号；保留 `manifest + load_result()`、文本校正不改时间轴和 snapshot 约束。

## 验证与交付顺序

1. 导入策略及两个 consumer 合同包检查：优先运行导入、setup、dependency checker 和 adapter 回归测试；同步环境前保留旧包拒绝证据，再同步复查。
2. 数据目录及日志错误边界：运行 logging、setup launcher/environment、CLI、job 恢复及删除相关测试。
3. 修复流程及字幕输出：运行 CLI 输出、Provider 选择、job 创建与 finalize、Bilibili pipeline 和恢复相关测试。
4. 跨模块改动通过聚焦测试后，在三个 Skill 中分别运行完整 Pytest、Ruff、格式检查和 Pyright，遵循各自 AGENTS.md；Qwen 环境禁止让开发工具启动时自动切换到 CPU。
5. 代码验证原则使用 uv 和 Python 3.12；配置正确后用 `uv run --no-sync` 执行测试和检查以保留待验证环境，开发检查工具缺失时明确记录并按当前 Provider 同步开发依赖。
6. 真实连续导入作为 Qwen 专项验证；不要求真实音频转写、完整 benchmark 或 Bilibili 网络任务来证明日志和版本检查修复。
7. 文档检查相对链接、路径、YAML 头、示例、冲突标记和 `git diff --check`。逐项报告通过、失败、未执行或受阻，不把模拟测试当作真实模型或网络验证。

建议按阶段形成可独立审查的改动；数据目录阶段每个 Skill 完成其所有入口及恢复/删除接入后再交付，避免中间状态出现路径规则不一致。
