# 错误处理

## 任务恢复

| 故障 | 处理 |
| --- | --- |
| ID、context、批量 JSON 或文本无效 | 修正参数重试；整批无部分提交，不猜测 ID。 |
| 工作副本缺失、JSON 损坏、分段或时间轴误改 | 声明和基准可信时执行 `transcript reset --id all`。查看、编辑和单段 reset 不能修复此类损坏。 |
| 基准缺失或 digest 不匹配 | 显式 `bind_transcription` 重新导入同音频 manifest；要求本地音频存在且身份不变。 |
| job 身份、结构或路径越界 | 停止，不用 reset 或绑定绕过校验；明确重建时才删除任务。 |
| 合法文本校正、SRT 缺失或陈旧 | 继续编辑或执行 `generate_srt`；打开任务会将不可用字幕返回为 `null`。 |
| 编辑或绑定提交异常 | 重新打开确认磁盘状态，不凭异常判断未提交；job 不可读时停止，不猜测或删除快照。 |
| 生成 SRT 失败 | 修复原因后重试生成；文件可能已写入但尚未发布，不凭存在就交付。 |

基准在编辑和 reset 期间不可变；文本无变化时保留路径与字幕声明。批量修改先校验全部项目再提交一次。发布和安全清理边界见[架构](ARCHITECTURE.md#持久化和提交)。旧 job 的兼容规则见[持久化兼容](ARCHITECTURE.md#持久化兼容)。

## 环境与依赖

- 依赖缺失、已安装包不一致、公共合同包版本或 API 不符：运行 setup 同步项目声明，再以 `--no-sync` 检查；相同失败持续时根据日志诊断，不反复同步。
- 日志、报告、数据目录或 `.venv` 写入失败：以实际失败路径和原始系统错误定位；数据写入可换可写 `--data-dir`，依赖同步仍要求 `.venv` 可写，不以 setup 修复文件系统权限。
- 项目文件缺失或声明无效：修复项目文件；uv 不可用、Python 版本或子进程错误：诊断工具和解释器，不自动删除现有 `.venv`。
- baseline、工作副本和时间轴错误属于任务数据问题，按上表恢复，不运行 setup。

检查器和 setup 复用 `contract_check.py`，根据 `pyproject.toml` 要求检查 distribution 版本和 `load_result`、`TranscriptionResult`、`ResultValidationError`、`PUBLIC_SCHEMA_VERSION=4`。缺 `packaging` 时仍报告 API 和其他检查结果。`uv pip check` 只验证已安装包之间的一致性，不能代替项目声明检查；实际绑定仍验证完整公共 bundle。

## 日志与文件系统

每次 setup、检查和业务 CLI 使用独立日志，固定在 `<data-dir>/.cache/logs/`；详细 traceback、第三方 logger 和 Python warnings 只写文件，状态与结果写 stdout，warning/error 写 stderr。不记录 transcript、用户源文本或编辑正文。失败时仅在日志确实可用时报告 `Full log` 路径；参数解析失败可以没有日志。

日志初始化失败就停止业务；报告发布失败也不能声称检查成功。按实际路径诊断权限，不自动归因于 ACL 或沙箱，不回退未知目录。setup 的 Python 日志开始前，uv 和 `.bat` 输出可能不受日志会话控制。

Windows 业务暂存目录使用随机名称加独占 `mkdir` 并继承父目录 ACL，不能用 `mkdtemp`、`TemporaryDirectory` 或 `mkdir(mode=0o700)`，避免排除沙箱身份。该限制不改变仓库要求的 Pytest 沙箱外执行。

数据目录及 uv 缓存配置见[环境与命令](OPERATIONS.md#环境配置)。切换数据目录不自动查找或迁移历史任务。
