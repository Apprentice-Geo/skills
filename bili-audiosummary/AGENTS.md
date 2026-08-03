# bili-audiosummary 开发说明

## 项目定位

`bili-audiosummary` 是面向 Bilibili 视频的音频总结 Agent Skill。它负责获取 Bilibili 资源、优先处理原生字幕、维护可恢复的 `summary_job.json`、生成总结 prompt 并校验最终总结。

本项目不拥有 ASR 模型、Provider、Execution Policy、转写缓存或 benchmark。需要转写时，Agent 把本地音频路径交给独立 `audio-transcribe` Skill，再把其公开 `result_manifest.json` 绝对路径传回本项目的继续命令。本项目不得导入另一个 Skill 的源码，但可以依赖固定版本的公开 `audio-transcribe-contract` 包。

主要入口与文档职责如下：

- `SKILL.md`：Agent 的 prepare/inspect/continue/complete 编排流程与能力边界。
- `README.md`：面向使用者的安装、命令、Cookie 和恢复说明。
- `scripts/run_pipeline.py`：资源准备和 `summary_job.json` 创建入口。
- `scripts/continue_summary.py`：外部结果契约与音频身份校验、Markdown/prompt 生成和状态迁移入口。
- `scripts/complete_summary.py`：原生字幕来源与最终总结校验、complete 状态迁移入口。
- `scripts/fetch_audio.py`：视频信息、字幕与音频资源获取。
- `scripts/subtitle_transcript.py`：原生 SRT 到本项目 transcript JSON 和统一 `transcript.md` 的转换。
- `scripts/validate_summary.py`：最终总结内容校验。
- `assets/`：总结模板与提示词资源。
- `references/ARCHITECTURE.md`：模块职责、job 状态和 artifact 边界。
- `references/ERROR-HANDLING.md`：故障定位、恢复与停止条件。
- `tests/`：与公开状态转换和脚本行为对应的测试。

## 开发边界

- 先读取当前实现、测试、拆分设计和相关文档，再判断现有契约；不要凭旧说明或旧记忆推断行为。
- 修改用户可见行为时，同步检查 `README.md`、`SKILL.md` 和 `references/` 中直接相关的内容。
- 保留 `summary_job.json` 固定顶层 shape；不可用字段写为 `null`，所有非绝对路径以 job 所在目录为基准。
- 外部转写目录只读。不得复制、重写或删除外部 transcript、时间戳、日志和 workspace。
- 外部结果必须通过 `audio-transcribe-contract` 校验，并与 job 音频 SHA-256 一致后才能接入。
- Bilibili 的语言参数只用于字幕选择，不得传给转写 Skill，也不得在本项目暴露 ASR 模型参数。
- `failed` 只用于 prepare 已写入 `preparing` 后的致命失败。continue 在发布前失败保持 `needs_transcription`；summary 校验失败保持 `prompt_ready`。
- 状态和 JSON artifact 使用唯一同目录临时文件与原子 replace 发布。不要通过只修改状态字符串绕过对应 artifact 校验。
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

提交前运行代码检查：

```powershell
uv run ruff check
uv run pyright
uv run ruff format --check .
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
- 交付前运行 `git diff --check`，并确认没有夹带生成文件、凭据、音频或无关改动。

## 提交信息

编写提交信息时，遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) 规范。
