# subtitle-creator 开发说明

本文件补充 `subtitle-creator` 特有的开发约束、入口与验证要求；仓库通用的编辑、测试执行环境和提交规范见[根目录开发说明](../AGENTS.md)。

## 项目定位

`subtitle-creator` 负责从本地音频创建或恢复 JSON／SRT 字幕，支持按源文本校正文字，并按细粒度时间戳间的停顿切分过长字幕。本 Skill 不运行 ASR、不下载音频；需要转写时由 Agent 调用独立的 `audio-transcribe` Skill，再通过 `audio-transcribe-contract` 公共合同包只读导入其 `manifest.json`。

主要入口与文档职责：

- `SKILL.md`：Agent 的任务打开、转写绑定、文本校正与字幕生成流程。
- `references/OPERATIONS.md`：环境配置、分段查看/编辑/reset 命令、生成参数与删除任务。
- `references/ARCHITECTURE.md`：模块职责、持久化和原子提交边界。
- `references/SUBTITLE-RENDERING.md`：断句、切分、校正回退和时间戳取整规则。
- `references/ERROR-HANDLING.md`：任务恢复、环境依赖与文件系统错误处理。
- `scripts/` 与 `tests/`：业务脚本、环境检查/setup 启动器及对应单元测试。

## 开发约束

- 合同包通过 GitHub 固定 tag 安装，业务模块与测试不得导入 `audio-transcribe` 源码或复制上游 schema 验证实现。
- 本地持久化结构、原子提交和安全清理必须遵循 `references/ARCHITECTURE.md`；渲染和切分逻辑遵循 `references/SUBTITLE-RENDERING.md`。
- `.cache/`、`.venv/`、`.pytest_cache/`、`.pytest_tmp/`、`.ruff_cache/`、`results/`、`tmp/` 和本地音频不纳入提交。
- 保持 Windows PowerShell 和 Python 3.12 兼容；仓库脚本通过 `uv run --no-sync python -m scripts.<module>` 运行。

## 常用开发命令

开发环境同步：

```powershell
uv sync --python 3.12
```

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

常见聚焦测试入口：

- 任务打开与绑定：`tests/test_open_subtitle_job.py`、`tests/test_bind_transcription.py`、`tests/test_transcription_input.py`
- 文本查看、编辑与 reset：`tests/test_transcript.py`、`tests/test_transcript_reset.py`
- 字幕生成与切分：`tests/test_generate_subtitle.py`、`tests/test_subtitle_splitting.py`
- 环境与启动器：`tests/test_check_dependencies.py`、`tests/test_contract_check.py`、`tests/test_setup_bootstrap.py`、`tests/test_windows_launchers.py`

## 提交信息

提交前运行相关测试、静态检查和 `git diff --cached --check`。提交格式见[仓库提交约定](../AGENTS.md#提交信息)。
