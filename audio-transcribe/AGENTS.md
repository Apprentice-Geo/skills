# audio-transcribe 开发说明

## 项目定位

`audio-transcribe` 是面向本地音频文件的转写 Agent Skill。它负责本地音频身份、语言检测、Provider 选择、ASR pipeline、转写缓存、公开 artifact 发布和可验证的转写结果复用。

本项目不下载媒体，不处理 Bilibili 元数据、字幕选择、总结 prompt 或 summary job，也不修改其他 Skill 的结果。其他 Skill 可以保存并读取本项目发布的 `result_manifest.json` 绝对路径，但两个 Skill 之间不得进行 Python import。

主要入口与文档职责如下：

- `SKILL.md`：Agent 的本地音频转写流程、公开结果读取方式和失败边界。
- `README.md`：面向使用者的安装、模型准备、CLI 参数和公开 artifact 说明。
- `scripts/transcribe.py`：CLI 解析、本地音频身份、语言解析、Provider 选择、variant 目录、锁、缓存复用和最终 manifest 输出。
- `scripts/asr/pipeline.py`：准备音频、VAD、分块规划、Provider 执行、chunk 校验、合并和 workspace 发布。
- `scripts/asr/providers/`：faster-whisper 与 Qwen3 的 Provider 适配层。
- `scripts/asr/execution/`：不同 Provider 的执行策略和执行身份。
- `scripts/asr/chunking/`：音频分块、时间线和规划优化。
- `scripts/asr/alignment.py`：chunk 级文本与时间戳 alignment 校验。
- `scripts/asr/merge.py`：把有序 chunk transcript 合并为 workspace result。
- `scripts/artifacts.py`：公开 artifact 与 manifest 的校验、发布和恢复。
- `scripts/model_identity.py`：固定模型 revision 与 `variant_id` 相关身份。
- `scripts/model_artifacts.py`：本地模型 artifact 就绪性检查。
- `scripts/process_logging.py`：转写日志归档。
- `scripts/setup/`：Windows 环境、依赖和模型安装辅助脚本。
- `references/architecture.md`：pipeline、result identity、cache 和公开 artifact 合同。
- `references/error-handling.md`：setup、模型、Provider、cache、artifact 和停止条件。
- `tests/`：与公开契约、CLI、pipeline、Provider、cache 和 setup 对应的测试。

## 开发边界

- 先读取当前实现、测试和引用文档，再判断现有契约；不要凭旧说明、旧设计或旧记忆推断行为。
- 修改用户可见行为时，同步检查 `README.md`、`SKILL.md` 和 `references/` 中直接相关的内容。
- `result_manifest.json` 是唯一公开入口，成功 manifest 必须是 `schema_version: 1` 且 `status: "complete"`。
- `workspace/` 是内部缓存与恢复目录，不是其他 Skill 的公开读取接口。不要把内部 `workspace/result.json` 当作跨 Skill 合同。
- 公开 artifact 路径以 manifest 所在目录为基准，必须校验 schema、status、identity、路径逃逸和 SHA-256 digest 后再读取。
- `audio_id` 是输入音频字节的 SHA-256，独立于输入路径和文件名。
- `variant_id` 来自规范化 resolved request，包含 Provider、语言、固定模型 revision、执行策略、VAD、规划和断句相关身份；不应包含输入路径、输出路径或日志级别。
- 公开 Provider 值只允许 `faster-whisper` 和 `qwen3`。
- 如果用户显式指定 `--provider`，只使用该 Provider；如果自动选择 Provider，一旦解析完成，后续加载、推理、alignment、merge 或发布失败不得静默切换 Provider。
- 公共 transcript 文本保持 Provider 原文，不执行 OpenCC、简繁转换、重写或其他文本规范化。
- 不要把第三方模型对象、原始 Provider 大对象或大型内部 metadata 写入公开 artifact。
- 不要手工修复 `result_manifest.json`、`transcript.json`、`raw_timestamps.json` 或 `workspace/` 内容；需要恢复时 rerun CLI，让 artifact 层按 digest 确定性重建。
- manifest 是唯一成功标记。发布新结果时先写 `transcript.json` 和 `raw_timestamps.json`，最后原子发布 `result_manifest.json`。
- cache hit 不应加载 ASR 模型、重跑推理、合并 chunk 或重写首次成功的 `transcribe.log`。
- `.cache/`、`.venv/`、`.pytest_cache/`、`.ruff_cache/`、`results/`、`tmp/`、`models/` 和本地音频均为本地或生成内容，不应纳入提交。
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

安装 Qwen3 可选依赖和模型：

```powershell
uv sync --python 3.12 --no-dev --extra qwen3
uv run --no-sync python -m scripts.setup.install_model --model qwen3
```

转写本地音频：

```powershell
uv run --no-sync python -m scripts.transcribe ".\audio.m4a"
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
uv run pytest tests/test_public_artifacts.py
uv run pytest tests/test_cli_output.py tests/test_asr_workspace.py
uv run pytest tests/test_asr_pipeline.py tests/test_asr_pipeline_runtime.py
```

真实转写依赖本地模型、CUDA 条件和输入音频。除非任务明确要求端到端验证，否则先使用常规单元测试和模拟边界完成验证。

## 测试与验证

- 修改 CLI 输出、public artifact、manifest、cache、result identity、Provider 解析、pipeline、alignment、merge 或 setup 时，运行对应的 `tests/test_*.py`。
- 修改 `result_manifest.json`、`transcript.json` 或 `raw_timestamps.json` 的公开合同，应覆盖合法和非法状态、路径逃逸、digest 不匹配、identity 不匹配、cache hit、artifact 损坏恢复和不可恢复失败。
- 修改 Provider adapter 时，保留第三方返回结构与本项目公开 artifact schema 的边界；必要时检查固定依赖版本的真实返回形状。
- 修改 execution policy、模型 revision、VAD、规划或断句身份时，确认 `variant_id` 会随合同变化而变化。
- 变更跨越多个模块或公共契约时，在聚焦测试通过后运行完整 `uv run pytest`。
- 文档改动至少检查命令、路径、模块名、Markdown 链接和冲突标记。
- 交付前运行 `git diff --check`，并确认没有夹带生成文件、模型文件、音频或无关改动。

## 提交信息

编写提交信息时，遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) 规范。
