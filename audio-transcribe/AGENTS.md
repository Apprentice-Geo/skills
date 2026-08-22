# audio-transcribe 开发说明

## 项目定位

`audio-transcribe` 是面向本地音频文件的转写 Agent Skill。它负责本地音频身份、语言检测、Provider 选择、ASR pipeline、转写缓存、公开 artifact 发布和可验证的转写结果复用。

本项目不下载媒体，不处理 Bilibili 元数据、字幕选择、总结 prompt 或 summary job，也不修改其他 Skill 的结果。其他 Skill 可以保存并读取本项目发布的 `result_manifest.json` 绝对路径，但两个 Skill 之间不得进行 Python import。

主要入口与文档职责如下：

- `SKILL.md`：Agent 的本地音频转写流程、公开结果读取方式和失败边界。
- `README.md`：面向使用者的项目概述、使用边界、隐私与进一步阅读说明。
- `scripts/`、`packages/`：转写、Provider、artifact、模型和环境实现；详细模块边界见 `references/ARCHITECTURE.md`。
- `references/ARCHITECTURE.md`：pipeline、result identity、cache 和公开 artifact 合同的唯一详细说明。
- `references/ERROR-HANDLING.md`：setup、模型、Provider、cache、artifact 和停止条件。
- `tests/`：与公开契约、CLI、pipeline、Provider、cache 和 setup 对应的测试。

## 开发边界

- 先读取当前实现、测试和引用文档，再判断现有契约；不要凭旧说明、旧设计或旧记忆推断行为。
- 修改用户可见行为时，同步检查 `README.md`、`SKILL.md` 和 `references/` 中直接相关的内容。
- `result_manifest.json` 是唯一公开入口；公开结果、身份、cache、恢复和 Provider 行为必须遵循 `references/ARCHITECTURE.md` 与其定义的 contract。
- 其他 Skill 只能通过公开的 `result_manifest.json` 和 `audio-transcribe-contract` 读取结果，不得读取或修改内部 `workspace/`。
- `.cache/`、`.venv/`、`.pytest_cache/`、`.pytest-tmp/`、`.ruff_cache/`、`results/`、`tmp/`、`models/` 和本地音频均为本地或生成内容，不应纳入提交。
- 保持 Windows PowerShell 和 Python 3.12 兼容；仓库脚本应通过 `uv run python -m scripts.<module>` 或 README 中记录的等价形式运行。

## 环境与常用命令

面向使用者的环境安装：

```powershell
.\scripts\setup\setup_windows.bat
```

开发环境同步：

```powershell
uv sync --python 3.12
```

安装 faster-whisper 模型：

```powershell
uv run --no-sync python -m scripts.setup.install_model --model faster-whisper
```

安装 Qwen3-ASR 可选依赖和模型：

```powershell
uv sync --python 3.12 --no-dev --extra qwen3-asr
uv run --no-sync python -m scripts.setup.install_model --model qwen3-asr
```

转写本地音频：

```powershell
uv run --no-sync python -m scripts.transcribe ".\audio.m4a"
```

首次准备 benchmark 数据并固定来源摘要：

```powershell
uv run --no-sync python -m benchmark.prepare_audio --pin-sha256
```

运行完整或筛选后的 benchmark：

```powershell
uv run --no-sync python -m scripts.benchmark
uv run --no-sync python -m scripts.benchmark --provider faster-whisper --language zh --minutes 8
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
uv run pytest tests/test_public_artifacts.py
uv run pytest tests/test_cli_output.py tests/test_asr_workspace.py
uv run pytest tests/test_asr_pipeline.py tests/test_asr_pipeline_runtime.py
uv run pytest tests/test_benchmark.py tests/test_cli_output.py
```

真实转写依赖本地模型、CUDA 条件和输入音频。除非任务明确要求端到端验证，否则先使用常规单元测试和模拟边界完成验证。

## 测试与验证

- 修改 CLI 输出、public artifact、manifest、cache、result identity、Provider 解析、pipeline、alignment、merge 或 setup 时，运行对应的 `tests/test_*.py`。
- 修改 `result_manifest.json`、`transcript.json` 或 `raw_timestamps.json` 的公开合同，应覆盖合法和非法状态、路径逃逸、digest 不匹配、identity 不匹配、cache hit、artifact 损坏恢复和不可恢复失败。
- 修改 Provider adapter 时，保留第三方返回结构与本项目公开 artifact schema 的边界；必要时检查固定依赖版本的真实返回形状。
- 修改 execution policy、模型 revision、VAD、规划或断句身份时，确认 `variant_id` 会随合同变化而变化。
- 变更跨越多个模块或公共契约时，在聚焦测试通过后运行完整 `uv run pytest`。
- 文档改动至少检查命令、路径、模块名、Markdown 链接和冲突标记。

## 提交信息

编写提交信息时，遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) 规范：

```
<type>(<scope>): <简短描述>

[可选的详细说明]
```
