# bili-audiosummary 开发说明

## 项目定位

`bili-audiosummary` 是面向 Bilibili 视频的音频总结 Agent Skill。项目运行在 Windows 和 Python 3.12 环境中，统一使用 `uv` 管理解释器与依赖。

主要入口与文档职责如下：

- `SKILL.md`：Agent 使用流程与能力边界。
- `README.md`：面向使用者的安装、运行和配置说明。
- `scripts/run_pipeline.py`：完整处理流程入口。
- `scripts/fetch_audio.py`：视频信息、字幕与音频资源获取。
- `scripts/transcribe.py`、`scripts/asr/`：转写入口与 ASR 相关实现。
- `scripts/validate_summary.py`：最终总结校验入口。
- `assets/`：总结模板与提示词资源。
- `references/architecture.md`：模块职责和数据流说明。
- `references/error-handling.md`：故障定位与处理说明。
- `tests/`：与各脚本和公开行为对应的测试。

## 开发边界

- 先读取当前实现、测试和相关文档，再判断现有契约；不要仅凭旧说明推断行为。
- 仓库当前的两条转写处理路径仍在开发。不要在本文件中把它们的选择、回退、并行、缓存或失败处理写成固定策略；涉及转写的改动应以当次需求、当前代码和对应测试为准。
- 修改用户可见行为时，同步检查 `README.md`、`SKILL.md` 和 `references/` 中与该行为直接相关的内容。
- `.cache/`、`.venv/`、`models/`、`results/`、`tmp/`、cookie 文件、音频和本地模型均为本地或生成内容，不应纳入提交。
- 保持 Windows PowerShell 和 Python 3.12 兼容；仓库脚本应通过 `python -m scripts.<module>` 形式运行。

## 环境与常用命令

面向使用者的核心环境安装：

```powershell
.\scripts\setup\setup_windows.bat
```

开发环境同步：

```powershell
uv sync --python 3.12
```

运行代码检查：

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
uv run pytest tests/test_transcribe.py
```

`tests/test_live_pipeline.py` 会访问真实 Bilibili 资源并依赖本地运行条件。除非任务明确需要端到端验证，否则使用常规单元测试和模拟边界完成验证。

## 测试与验证

- 修改 pipeline、资源获取、转写、总结校验或 setup 时，运行对应的 `tests/test_*.py`。
- 变更跨越多个模块或公共契约时，在聚焦测试通过后运行完整 `uv run pytest`。
- 测试临时文件使用 `tests/conftest.py` 提供的 `workspace_tmp_path`；不要重新引入固定的系统临时目录或仓库外测试目录。
- 文档改动至少检查命令、路径和模块名是否与当前仓库一致。
- 交付前运行 `git diff --check`，并确认没有夹带生成文件、凭据、模型、音频或无关改动。

## 提交信息

编写提交信息时，遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) 规范。
