# audio-transcribe 开发说明

本文件面向执行仓库任务的 Agent 和维护这些规则的维护者。它定义仓库级工作约束、权威来源、任务路由和验证要求；运行时流程、公开合同、错误处理和 benchmark 方法由各自的长期文档负责。

## 项目范围

`audio-transcribe` 是面向本地音频文件的转写 Agent Skill，负责音频身份、语言检测、Provider 选择、ASR pipeline、转写 cache、公开 artifact 发布和结果复用。

生产 Skill 不下载媒体，也不处理 Bilibili 元数据、字幕选择、总结 prompt 或 summary job。开发用 benchmark 可以通过其明确的数据准备流程下载固定测试素材，但不得把该能力引入生产转写路径。其他 Skill 只能通过公开的 `result_manifest.json` 和 `audio-transcribe-contract` 使用结果，不得 import 本项目源码或读取、修改内部 `workspace/`。

## 权威来源

- `SKILL.md`：Agent 的本地音频转写流程、依赖检查、公开结果读取方式和停止边界。
- `README.md`：面向使用者的项目概述、使用边界、隐私和进一步阅读入口。
- `references/ARCHITECTURE.md`：pipeline、result identity、cache 和公开 artifact 合同的唯一详细说明。
- `references/ERROR-HANDLING.md`：setup、模型、Provider、cache、artifact 和停止条件的唯一详细说明。
- `benchmark/README.md`：benchmark 的方法、数据、reference、运行、恢复、报告和维护规则的唯一详细说明。
- `scripts/`、`packages/` 和 `tests/`：当前可观察行为及其验证证据。

仓库根目录下 `references/` 中除上述长期文档外，可能包含阶段性计划、调查、已知问题记录或 benchmark 报告。这些文件是临时工作材料，不是当前合同或长期引用目标；仅在任务明确相关时读取，是否删除由维护者决定，Agent 不得仅因其临时地位自行删除。

长期文档定义预期合同，实现和测试反映当前可观察行为。三者不一致时，将其视为需要调查的缺陷；不得静默选择其中一方，或仅修改另一方来消除表面差异。

## 全局开发约束

- 修改前读取当前实现、相关测试和对应权威文档；不要依据旧说明、阶段性计划或记忆推断当前行为。
- 修改用户可见行为时，同步检查 `README.md`、`SKILL.md` 及直接相关的长期文档。
- 保持 Windows PowerShell 和 Python 3.12 兼容。运行仓库 Python 模块时，应使用 `uv run --no-sync python -m <module>` 或长期文档记录的等价命令；开发工具使用下文记录的 `uv run <tool>` 形式。
- `.cache/`、`.venv/`、`.pytest_cache/`、`.pytest-tmp/`、`.ruff_cache/`、`models/`、`results/`、`tmp/`、`benchmark/data/`、`benchmark/tmp/`、`benchmark/reports/` 和本地音频均为本地或生成内容，不应纳入提交。
- 相同规则应只有一个详细来源。AGENTS.md 可以保留高风险边界的简短摘要，但不得复制易变化的字段、schema、算法步骤或错误枚举。

## 任务路由与验证

| 改动范围 | 修改前读取 | 优先验证 |
| --- | --- | --- |
| CLI 或用户可见转写行为 | `README.md`、`SKILL.md` | `tests/test_cli_output.py` 及直接相关测试 |
| manifest、公开 artifact、result identity、cache 或恢复 | `README.md`、`SKILL.md`、`references/ARCHITECTURE.md`、`references/ERROR-HANDLING.md` | `tests/test_result_contract.py`、`tests/test_public_artifacts.py`、`tests/test_asr_workspace.py` |
| pipeline、VAD、规划、alignment 或 merge | `references/ARCHITECTURE.md` | `tests/test_asr_chunking.py`、`tests/test_asr_pipeline.py`、`tests/test_asr_pipeline_runtime.py`、`tests/test_qwen3_alignment.py` 中与改动相关的测试 |
| Provider、execution policy、模型 revision 或 setup | `references/ARCHITECTURE.md`、`references/ERROR-HANDLING.md` | `tests/test_asr_providers_and_policies.py`、`tests/test_setup_revisions.py`、`tests/test_check_dependencies.py` 及相关 pipeline 测试 |
| benchmark runner、worker、指标、数据、reference、报告或恢复 | `benchmark/README.md` | 按 `benchmark/README.md` 的维护规则选择聚焦测试 |
| 纯文档 | 对应长期文档 | 检查命令、路径、模块名、Markdown 链接和冲突标记，并运行 `git diff --check` |

任何可能改变 transcript 字节或 timestamp 的 resolved behavior 都必须纳入结果身份。修改这类行为时，以 `references/ARCHITECTURE.md` 的 identity contract 为准，并通过上述 result identity、pipeline 或 Provider 测试中受影响的 identity assertions 验证，不在本文件重复列举其组成字段。

先运行与改动直接相关的聚焦测试。变更跨越多个模块或公共合同时，在聚焦测试通过后运行完整测试。真实转写依赖本地模型、CUDA 条件和输入音频；除非任务明确要求端到端证据，否则使用单元测试和模拟边界验证。

真实 benchmark 会加载本地模型、预热并处理长音频，可能长时间占用 CPU、GPU、内存和磁盘。不需要真实模型结果的普通实现或文档改动默认只运行 benchmark 单元测试；只有任务明确需要真实性能或准确率证据时，才运行最小范围的筛选 benchmark。完整矩阵以及 `benchmark.prepare_audio --pin-sha256` 仅在用户以维护者身份明确要求时运行。未运行真实 benchmark 时，应准确报告验证范围，不得声称已验证真实性能、RTF 或转写准确率。

## 常用开发命令

开发环境同步：

```powershell
uv sync --python 3.12
```

静态检查和完整测试：

```powershell
uv run ruff check
uv run pyright
uv run ruff format --check .
uv run pytest
git diff --check
```

setup、模型安装和真实转写命令见 `SKILL.md` 与 `references/ERROR-HANDLING.md`；benchmark 命令见 `benchmark/README.md`。

## 提交信息

编写提交信息时，遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) 规范：

```
<type>(<scope>): <简短描述>

[可选的详细说明]
```

提交前除相关测试外，运行上述静态检查，并对已暂存内容运行 `git diff --cached --check`。

## 维护本文件

- 只有仓库级工作方式、权威来源、任务路由或验证政策变化时，才修改 AGENTS.md。
- 领域行为变化应更新其权威文档；除非路由或仓库级边界同时变化，否则不要把细节同步复制到 AGENTS.md。
- 不在本文件记录临时进度、阶段性问题、单次调查结论或带日期的 benchmark 结果。
- 删除或重命名长期文档、命令、模块或测试时，同步检查本文件中的路由和链接。
