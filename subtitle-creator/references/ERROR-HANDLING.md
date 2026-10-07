# 错误处理

## 运行目录与写入失败

`scripts/runtime_paths.py` 是本 Skill 的运行数据路径来源。CLI 的 `--data-dir` 优先于 `SUBTITLE_CREATOR_DATA_DIR`，未配置时使用 Skill 根目录。布局保留 `<data-dir>/.cache/logs/`、`<data-dir>/.cache/uv/` 和 `<data-dir>/results/`；转写 workspace 跟随结果目录，字幕和总结的任务内 artifact 跟随各自 job。活动日志确定可信 job 后仍可移入 job，删除日志保留在 cache 中。

所有命令启动时解析绝对路径，再传给业务入口。源码、模板、`.venv`、已安装模型、第三方库及系统临时目录不因数据目录设置而迁移。显式 `UV_CACHE_DIR` 优先；直接使用 `uv run` 时应在启动 uv 前设置它。历史任务在原目录继续可用；切换根目录不自动查找或迁移其他目录的任务，字幕 job 已保存的绝对 artifact 路径语义不变。

创建目录或打开日志失败时，最外层 CLI 直接向 stderr 输出操作、绝对失败路径、异常类型和原始系统错误，退出码为 `1`；明确日志未创建，不输出 `Full log`，不继续业务或安装依赖，不回退到未知目录。日志初始化失败恢复 logger、warnings hook 和 handler 状态。根据失败路径选择显式可写的数据目录再执行；不能仅凭访问被拒绝就断定是 Windows ACL 或沙箱，也不能把文件系统错误当作缺包并运行 setup。

依赖检查通过但 JSON 报告发布失败时仍返回 `1`，单独报告发布失败；只有已建立的日志才输出 `Full log`，没有成功发布的报告不得输出成功报告路径。日志移动失败时重新打开原日志并继续；原日志或移动后的日志无法重新打开时停止，报告真实路径和系统错误，不声称活动日志可用。检查器关键前置错误仍使用原有退出码。

数据目录可写只解决运行数据写入。依赖同步需要 `.venv` 可写，模型安装需要模型目录可写；应分别根据真实失败路径诊断。setup 不为初始化 cache 或日志顺便创建模型或结果目录。

Windows 下的本地快照、发布和下载暂存目录使用随机名称加独占 `mkdir` 创建，继承父目录 ACL；不得使用 `mkdtemp`、`TemporaryDirectory` 或 `mkdir(mode=0o700)` 创建这类业务目录，否则可能排除沙箱身份并导致创建后写入失败。该规则不改变 Pytest 的沙箱外执行要求。已有目录访问失败时仍按真实路径诊断，不通过更改全局 ACL 或反复重试绕过。

## 日志与终端输出

每次 setup、dependency checker 和 workflow CLI 调用使用独立日志。Python 日志会话把同一条业务记录同时写入文件和指定终端：状态与结果写入 stdout，显式 warning 和 error 写入 stderr，调试明细、第三方 logger、Python warnings 及完整 traceback 仅写入文件。不得记录 transcript 正文、用户提供的源文本或上游模型对象。

以下输出不属于 Python 日志会话：argparse 的 `--help` 和解析器自身输出、`.bat` 的 `where uv` 与路径切换检查，以及 `uv python install 3.12` 的提示、原始输出和失败输出。若 Python bootstrap 尚未启动，setup 失败时可以没有 setup 日志，也不得声称存在 `Full log`。

dependency checker 将 JSON 原子发布到 `.cache/logs/`，并从同一份 report 生成日志记录。PASS 明细只进入文件，聚合结果和报告路径进入 stdout，WARN 与 FAIL 进入 stderr；退出码保持 `0`、`1`、`2` 三档语义。

## Workflow 日志位置

`open_subtitle_job`、`bind_transcription`、`reset_transcript` 和 `generate_srt` 先在 `.cache/logs/` 创建日志。只有在命令成功获得并验证可信 job 路径后，才把活动日志移动到对应的 `results/<audio-id>/`；最终结果记录在移动后写入。移动失败时继续使用 cache 中的原日志，并在日志内记录 traceback，不改变命令结果。

`remove_subtitle_job` 的日志始终保留在 `.cache/logs/`，避免日志随 job 目录删除或产生打开句柄冲突。任何 workflow 在获得可信 job 路径前失败时，日志也留在 cache。

`open_subtitle_job` 成功时 stdout 为单行 JSON，字段为 `status`、`subtitle_job`、`audio_path`、`normalized_transcript`、`subtitle`；格式与恢复决策见 [打开任务](../SKILL.md#1-从音频打开任务)。摘要不修改 job、不生成字幕。其他命令保持原有的一行结果：

```text
normalized_transcript: <absolute-path>
subtitle: <absolute-path>
removed_subtitle_job: <absolute-path>
subtitle_job_absent: <absolute-path>
```

失败时 stdout 为空，stderr 包含简洁错误；日志可用时附带 `Full log` 路径。根据日志修复输入或环境后，从上一个成功状态重试；不得根据未发布的临时文件推断成功。


## 任务恢复

- 工作副本缺失、JSON 损坏或分段、时间轴被误改：若任务声明与基准可信，运行 `reset_transcript`，再使用其返回路径继续校正和生成。
- 基准缺失或 digest 不匹配：reset 拒绝恢复；用 `bind_transcription` 重新导入有效且属于同一音频的 manifest。绑定仍要求本地音频存在且内容身份未改变。
- job 身份、结构或产物路径越界：停止；不能通过 reset 或绑定绕过声明校验。明确需要重建时才删除单个任务。
- 绑定或 reset 写入失败：job 切换前旧绑定和校正保持不变；忽略未提交快照，修复原因后重试。旧快照与未提交目录随整个任务删除，不能据此推断当前绑定。
- 生成 SRT 失败：job 不发布新声明；SRT 可能已写入，不能仅根据文件存在交付，修复原因后重新运行生成。每次生成都会覆盖文件，不复用已有字幕。
- 旧 job schema `2` 可直接读取并保留校正；成功写入时使用 schema `3`，转写 JSON 格式不变。兼容边界见[架构](ARCHITECTURE.md#持久化兼容)。

## 环境修复分类

- 必需依赖缺失、已安装包不一致、合同包版本不符或公共 API 不完整：显式运行一次 setup 同步当前项目声明，再以 `--no-sync` 复查；仍失败时停止，不重复 setup。
- 日志或报告写入失败、数据或 `.venv` 路径权限错误：按真实失败路径诊断，运行数据问题可显式指定可写数据目录；不以 setup 修复文件系统权限。
- 项目文件缺失、声明不可读或无效：定位并修复项目文件，不把这些错误当作合同包缺失。
- uv 不可用、Python 版本不符、子进程无法启动：诊断工具与解释器；不自动删除或替换现有 `.venv`。
- baseline 损坏、工作副本缺失、非法时间轴属于任务输入错误；按[任务恢复](#任务恢复)选择 reset 或重新绑定，不运行 setup。合法文本编辑和陈旧 SRT 保持可恢复，打开输出 `subtitle: null`，通过 `generate_srt` 重建。

## 合同包检查

检查器和 setup 复用本 Skill 的 `scripts/contract_check.py`：从 `pyproject.toml` 读取合同包要求，通过当前解释器的 `importlib.metadata` 读取 distribution 版本，检查 `load_result`、`TranscriptionResult`、`ResultValidationError` 和 `PUBLIC_SCHEMA_VERSION=3`。版本不符、包缺失、声明不可读或无效、API 不完整时返回失败，不能报告 ready。JSON 检查项包含 expected、actual 和同步建议。

缺少 `packaging` 时，检查器仍输出日志与 JSON 报告；`contract:version` 标记失败并给出依赖同步建议，公共 API 和其他检查继续执行。

`uv pip check` 仅检查已安装包之间的一致性，不证明它们满足当前项目声明。按本 Skill 环境策略显式同步后，使用 `uv run --no-sync python -m scripts.check_dependencies` 复查，避免自动同步掩盖旧包问题。实际接入转写时仍通过 `load_result()` 验证完整公共 bundle；能力检查不能代替该验证。
