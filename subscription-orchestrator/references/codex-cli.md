# Codex CLI 参考

本文档记录在本地验证过的 Codex CLI（`codex-cli 0.172.0`）调用约定与常见注意事项，供跨 Harness 调用 Codex 时按需查阅。

## 本地验证基线

- 认证检查：`codex login status` 返回 `Logged in using ChatGPT`（退出码 `0`）。
- 目标模型：`gpt-6.2-sol`
- 支持的推理强度（`model_reasoning_effort`）：`low`、`medium`、`high`、`xhigh`、`max`、`ultra`。
- 默认配置位于 `~/.codex/config.toml`，模型缓存位于 `~/.codex/models_cache.json`。

## 推荐调用方式（通过封装脚本）

优先通过本 Skill 的 `run-codex` 调用，自动处理 stdin 传参、防递归护栏、剔除 `OPENAI_API_KEY` 和限额退出码识别：

```powershell
# 只读分析或诊断
uv run --no-sync python -m scripts.orchestrator run-codex --effort high --cwd "<dir>" --prompt "<问题与文件路径>"

# 工程编码（显式允许工作区写入）
uv run --no-sync python -m scripts.orchestrator run-codex --sandbox workspace-write --effort high --cwd "<dir>" --prompt-file "<prompt.txt>"

# 独立代码审查（默认审查未提交变更）
uv run --no-sync python -m scripts.orchestrator run-codex --review --uncommitted --cwd "<repo-dir>"

# 按分支或提交审查
uv run --no-sync python -m scripts.orchestrator run-codex --review --base main --cwd "<repo-dir>"
uv run --no-sync python -m scripts.orchestrator run-codex --review --commit <sha> --cwd "<repo-dir>"

# 预览将执行的精确命令（不实际调用）
uv run --no-sync python -m scripts.orchestrator run-codex --dry-run --sandbox workspace-write --prompt "test"
```

## 原始 CLI 命令速查

需要脱离封装脚本直接执行时，遵循以下已验证参数：

| 场景 | 命令模板 |
| --- | --- |
| 非交互只读执行 | `codex exec -m gpt-6.2-sol -c model_reasoning_effort=high -s read-only -C "<dir>" -`（通过 stdin 传入 prompt） |
| 允许工作区写入 | `codex exec -m gpt-6.2-sol -c model_reasoning_effort=high -s workspace-write -C "<dir>" -` |
| 附加参考图片 | 在 `codex exec` 后增加 `-i "<image-path>"`（可重复） |
| 写入最终回复到文件 | 增加 `-o "<result.md>"` |
| 结构化 JSON 输出 | 增加 `--output-schema "<schema.json>"` 或 `--json`（JSONL 事件流） |
| 独立代码审查 | `codex exec review -m gpt-6.2-sol -c model_reasoning_effort=high --uncommitted` |
| 续接最近会话 | `codex exec resume --last "<follow-up>"` |
| 命令行图像生成 | `codex exec -m gpt-6.2-sol -s workspace-write -C "<dir>" '$imagegen <说明与目标保存路径>'` |

## 实操注意事项

- **`codex exec review` 参数差异**：`codex exec review` 不接受 `-s`/`--sandbox` 和 `-C`/`--cd`；切换目录必须通过子进程 `cwd` 实现（`run-codex --review --cwd` 已自动处理）。
- **Windows 多行提示词**：不要在 PowerShell 中把含引号或换行的长文本直接拼进命令行参数，统一通过 stdin（`-`）或 `--prompt-file` 传递。
- **图像生成（`$imagegen`）**：Codex 内置 `$imagegen` Skill 使用 ChatGPT Plus / Codex 订阅额度，需要 `--sandbox workspace-write` 才能将图片写入本地文件；支持配合 `-i` 输入参考图进行编辑或衍生生成。
- **非 Git 目录**：在非 Git 仓库执行时需追加 `--skip-git-repo-check`。
- **额度与计费**：
  - ChatGPT Work 与 Codex 共享 Agent 额度；剩余额度面板见 `https://chatgpt.com/codex/settings/usage`（交互式终端中可用 `/status`）。
  - OpenAI API 独立计费，不包含在 ChatGPT Plus 中；切勿通过设置 `OPENAI_API_KEY` 绕过订阅限额。
