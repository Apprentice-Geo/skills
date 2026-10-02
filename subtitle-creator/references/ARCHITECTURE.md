# 架构

## 模块边界

- `scripts/runtime_paths.py` 在命令启动时解析数据目录；创建、校验、关联、完成和删除使用同一个结果根目录。
- `scripts/create_subtitle.py` 按音频 SHA-256 创建或恢复任务；`scripts/subtitle_job.py` 维护 job 和 artifact 的合法性、文本校正边界及 SRT 有效性。
- `scripts/transcription_input.py` 通过公共 `load_result()` 只读接入转写；`scripts/attach_transcription.py` 发布本地 normalized transcript 与 baseline。
- `scripts/finalize_subtitle.py` 从合法 normalized transcript 发布或恢复 SRT；`scripts/remove_subtitle_job.py` 仅删除当前配置根目录下单个合法任务，拒绝 symlink、junction 和越界。
- `scripts/process_logging.py` 负责会话状态、文件与终端路由、子进程日志和失败边界；setup 与检查器各自复用合同包检查。

## 控制流与数据边界

命令先解析参数与绝对运行路径，再建立日志；启动日志失败时停止，不执行依赖安装或 job 操作。创建后任务为 `needs_transcription`，导入经过公共合同验证的转写后为 `editable`。后续校正只改本地分段 `text`，不改时间轴；finalize 校验 baseline 和当前文本，发布字幕与任务声明。合法文本编辑和陈旧 SRT 可通过既有恢复入口继续。

创建命令从已验证的 job 输出单行 JSON 摘要，直接提供状态与可用 artifact 的绝对路径；未就绪、缺失、损坏或与当前文本不一致的 SRT 输出 `null`。摘要与 job 校验复用同一 SRT 有效性规则，不修改任务或隐式执行 finalize，不新增持久化字段或状态。Agent 按摘要选择转写或编辑/finalize，非法时间轴与损坏 baseline 仍停止。

日志、报告、内部缓存和结果使用当前数据目录；源码、`.venv`、模板及外部转写目录独立。默认布局与历史任务继续可用；切换目录不搜索、迁移或合并旧任务。job 内绝对 artifact 路径保持原 schema 语义。详细配置、日志移动和写入失败处理见 [错误处理](ERROR-HANDLING.md#运行目录与写入失败)。
