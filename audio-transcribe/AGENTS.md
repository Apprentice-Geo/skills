# audio-transcribe 开发说明

本文件补充 `audio-transcribe` 特有的开发约束、权威来源、任务路由和验证要求；仓库通用的编辑、测试执行环境和提交规范见[根目录开发说明](../AGENTS.md)。运行时流程、公开合同、错误处理和 benchmark 方法由各自的长期文档负责。

## 项目范围

`audio-transcribe` 是面向本地音频文件的转写 Agent Skill，负责音频身份、语言检测、Provider 选择、ASR pipeline、转写 cache、公开 artifact 发布和结果复用。

生产 Skill 不下载媒体，也不处理 Bilibili 元数据、字幕选择、总结 prompt 或 summary job。开发用 benchmark 可以通过其明确的数据准备流程下载固定测试素材，但不得把该能力引入生产转写路径。其他 Skill 只能通过公开的 `manifest.json` 和 `audio-transcribe-contract` 使用结果，不得 import 本项目源码或读取、修改内部 `workspace/`。

## 权威来源

- `SKILL.md`：Agent 的本地音频转写流程、依赖检查、公开结果读取方式和停止边界。
- `README.md`：面向使用者的项目概述、使用边界、隐私和进一步阅读入口。
- `references/ARCHITECTURE.md`：pipeline、result identity、cache 和公开 artifact 合同的唯一详细说明。
- `references/ERROR-HANDLING.md`：setup、模型、Provider、cache、artifact 和停止条件的唯一详细说明。
- [references/DEVELOPMENT.md](references/DEVELOPMENT.md)：暂存目录、日志初始化、依赖兼容、模型身份、alignment 与发布诊断的开发实现约束。
- `benchmark/README.md`：benchmark 的方法、数据、reference、运行、恢复、报告和维护规则的唯一详细说明。
- `scripts/`、`packages/` 和 `tests/`：当前可观察行为及其验证证据。

## 开发约束

- 保持 Windows PowerShell 和 Python 3.12 兼容。运行 Python 模块和开发工具时，使用 `uv run --no-sync python -m <module>` 或长期文档记录的等价命令；不因验证自动同步依赖。
- `.cache/`、`.venv/`、`.pytest_cache/`、`.pytest-tmp/`、`.ruff_cache/`、`models/`、`results/`、`tmp/`、`benchmark/data/`、`benchmark/tmp/`、`benchmark/reports/` 和本地音频均为本地或生成内容，不应纳入提交。
- 相同规则只保留一个详细来源。本文件可保留高风险边界的简短摘要，不得复制易变化的字段、schema、算法步骤或错误枚举。

## 任务路由与验证

| 改动范围 | 修改前读取 | 优先验证 |
| --- | --- | --- |
| CLI 或用户可见转写行为 | `README.md`、`SKILL.md` | `tests/test_cli_output.py` 及直接相关测试 |
| manifest、公开 artifact、result identity、cache 或恢复 | `README.md`、`SKILL.md`、`references/ARCHITECTURE.md`、`references/ERROR-HANDLING.md` | `tests/test_result_contract.py`、`tests/test_public_artifacts.py`、`tests/test_asr_workspace.py` |
| pipeline、VAD、规划、alignment 或 merge | `references/ARCHITECTURE.md` | `tests/test_asr_chunking.py`、`tests/test_asr_pipeline.py`、`tests/test_asr_pipeline_runtime.py`、`tests/test_qwen3_alignment.py` 中与改动相关的测试 |
| Provider、execution policy、模型 revision 或 setup | `references/ARCHITECTURE.md`、`references/ERROR-HANDLING.md` | `tests/test_asr_providers_and_policies.py`、`tests/test_setup_revisions.py`、`tests/test_check_dependencies.py` 及相关 pipeline 测试 |
| benchmark runner、worker、指标、数据、reference、报告或恢复 | `benchmark/README.md` | 按 `benchmark/README.md` 的维护规则选择聚焦测试 |
| 纯文档 | 对应长期文档 | 检查命令、路径、模块名、Markdown 链接和冲突标记，并运行 `git diff --check` |

修改暂存目录、日志初始化、依赖导入兼容、模型身份校验、alignment 接受或恢复与发布诊断实现时，按[开发实现约束](references/DEVELOPMENT.md)的对应章节读取已验证的易错点；普通转写、安装和恢复任务无需加载该文档。

任何可能改变 transcript 字节或 timestamp 的 resolved behavior 都必须纳入结果身份。修改这类行为时，以 `references/ARCHITECTURE.md` 的 identity contract 为准，并通过上述 result identity、pipeline 或 Provider 测试中受影响的 identity assertions 验证，不在本文件重复列举其组成字段。

真实转写依赖本地模型、CUDA 条件和输入音频；除非任务明确要求端到端证据，否则使用单元测试和模拟边界验证。

真实 benchmark 会加载本地模型、预热并处理长音频，可能长时间占用 CPU、GPU、内存和磁盘。不需要真实模型结果的普通实现或文档改动默认只运行 benchmark 单元测试；只有任务明确需要真实性能或准确率证据时，才运行最小范围的筛选 benchmark。完整矩阵以及 `benchmark.prepare_audio --pin-sha256` 仅在用户以维护者身份明确要求时运行。未运行真实 benchmark 时，应准确报告验证范围，不得声称已验证真实性能、RTF 或转写准确率。

## 常用开发命令

开发环境同步：

```powershell
uv sync --python 3.12 --extra cpu
```

`cpu` 与 `qwen3-asr` extra 互斥，安装与切换规则见 [Setup 与依赖](references/ERROR-HANDLING.md#setup-与依赖)。

静态检查：

```powershell
uv run --no-sync python -m ruff check
uv run --no-sync python -m pyright
uv run --no-sync python -m ruff format --check .
git diff --check
```

完整测试范围如下；实际执行权限、独立目录、缓存与清理遵循[根目录测试执行环境规则](../AGENTS.md#测试执行环境)：

```powershell
uv run --no-sync python -m pytest
```

setup、模型安装和真实转写命令见 `SKILL.md` 与 `references/ERROR-HANDLING.md`；benchmark 命令见 `benchmark/README.md`。

## 提交信息

提交前运行相关测试和上述静态检查，对已暂存内容运行 `git diff --cached --check`。提交格式见[仓库提交约定](../AGENTS.md#提交信息)。

## 维护本文件

- 只有本 Skill 的开发边界、权威来源、任务路由或验证政策变化时，才修改本文件。
- 领域行为变化应更新其权威文档；除非路由或开发边界同时变化，否则不要把细节同步复制到本文件。
- 不在本文件记录临时进度、阶段性问题、单次调查结论或带日期的 benchmark 结果。
- 删除或重命名长期文档、命令、模块或测试时，同步检查本文件中的路由和链接。
