# bili-audiosummary 开发说明

## 项目定位

`bili-audiosummary` 是面向 Bilibili 视频的音频总结 Agent Skill。它负责获取 Bilibili 资源、优先处理原生字幕、维护可恢复的 `summary_job.json`、生成总结 prompt 并校验最终总结。

本项目不拥有 ASR 模型、Provider、Execution Policy、转写缓存或 benchmark。需要转写时，Agent 把本地音频路径交给独立 `audio-transcribe` Skill，再把其公开 `result_manifest.json` 绝对路径传回本项目的继续命令。本项目不得导入另一个 Skill 的源码，但可以依赖固定版本的公开 `audio-transcribe-contract` 包。

主要入口与文档职责如下：

- `SKILL.md`：Agent 的 prepare/inspect/continue/complete 编排流程与能力边界。
- `README.md`：面向使用者的项目概述、使用边界、隐私与进一步阅读说明。
- `scripts/`：资源准备、外部转写接入、字幕转换和总结完成；详细模块边界见 `references/ARCHITECTURE.md`。
- `assets/`：总结模板与提示词资源。
- `references/ARCHITECTURE.md`：模块职责、job 状态和 artifact 边界。
- `references/ERROR-HANDLING.md`：故障定位、恢复与停止条件。
- `tests/`：与公开状态转换和脚本行为对应的测试。

## 开发边界

- 先读取当前实现、测试、拆分设计和相关文档，再判断现有契约；不要凭旧说明或旧记忆推断行为。
- 修改用户可见行为时，同步检查 `README.md`、`SKILL.md` 和 `references/` 中直接相关的内容。
- `summary_job.json`、状态迁移、外部转写接入、artifact 和恢复行为必须遵循 `references/ARCHITECTURE.md` 与其定义的 contract。
- 外部转写目录只读；其他 Skill 的结果只能通过公开的 `audio-transcribe-contract` 接入。
- 对于实验性代码或临时脚本，在确认增加到项目中前，不要为它们编写测试或同步文档。
- `.cache/`、`.venv/`、`results/`、`tmp/`、cookie 文件和音频均为本地或生成内容，不应纳入提交。
- 保持 Windows PowerShell 和 Python 3.12 兼容；仓库脚本应通过 `uv run python -m scripts.<module>` 形式运行。

## 环境与常用命令

面向使用者的环境安装：

```powershell
.\scripts\setup\setup_windows.bat
```

开发环境同步：

```powershell
uv sync --python 3.12
```

提交前运行检查：

```powershell
uv run ruff check
uv run pyright
uv run ruff format --check .
git diff --cached --check
```

运行完整测试：

```powershell
uv run pytest
```

优先针对改动范围运行聚焦测试，例如：

```powershell
uv run pytest tests/test_run_pipeline.py
uv run pytest tests/test_continue_summary.py tests/test_complete_summary.py
```

真实流程测试会访问 Bilibili，外部转写流程还依赖单独安装的 `audio-transcribe`。除非任务明确要求端到端验证，否则先使用常规单元测试和模拟边界完成验证。

## 测试与验证

- 修改 pipeline、资源获取、job 状态、外部 transcript 定位、总结校验或 setup 时，运行对应的 `tests/test_*.py`。
- job 测试应覆盖合法和非法状态转换、路径逃逸、manifest 幂等绑定，以及 summary 的失败和成功。
- 变更跨越多个模块或公共契约时，在聚焦测试通过后运行完整 `uv run pytest`。
- 测试临时文件使用 `tests/conftest.py` 提供的 `workspace_tmp_path`；不要重新引入固定的系统临时目录或仓库外测试目录。
- 文档改动至少检查命令、路径、模块名、Markdown 链接和冲突标记。

## 提交信息

编写提交信息时，遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) 规范：

```
<type>(<scope>): <简短描述>

[可选的详细说明]
```