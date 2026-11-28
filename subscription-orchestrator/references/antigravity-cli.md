# Antigravity CLI (`agy`) 参考

本文档记录在本地验证过的 Antigravity CLI（`agy.exe 1.5.18`）调用约定、精确模型 ID 与 Opus 5.5 额度保护规则，供跨 Harness 调用 Antigravity 时按需查阅。

## 本地验证基线

- Windows 默认安装位置：`%LOCALAPPDATA%\agy\bin\agy.exe`（若父桌面应用在安装 `agy` 前启动，当前进程 `PATH` 可能尚未包含该目录，封装脚本会自动回退查找）。
- 可用性及登录态检查：`agy models` 能正常返回模型列表即表明已登录并可访问服务。
- 重点目标模型（实测 `agy models` 输出的精确 slug）：

| 模型家族 | 实测可用 slug | 职责定位 |
| --- | --- | --- |
| Gemini 4 Neon | `gemini-4-neon-low`、`gemini-4-neon-medium`、`gemini-4-neon-high`、`gemini-4-neon-xhigh` | UI/前端/可视化创意的编码实现与规划；Codex 额度不足时可接管工程任务 |
| Claude Opus 5.5 | `claude-opus-5-5-low`、`claude-opus-5-5-medium`、`claude-opus-5-5-high` | **仅用于 UI/前端/可视化等不写代码的设计与规划**（额度极少） |

> 注意：这两组模型的推理强度已直接编码在模型 slug 后缀中（如 `-high`、`-xhigh`）。使用带后缀的精确 slug 时，无需再叠加可能冲突的 `--effort` 参数；封装脚本会自动处理这一映射。

## 推荐调用方式（通过封装脚本）

优先通过本 Skill 的 `run-agy` 调用，自动解析 `agy.exe` 路径、映射精确模型 slug、执行 Opus 5.5 只读规划拦截、屏蔽 `GEMINI_API_KEY` / `GOOGLE_API_KEY` 并检测限额退出码：

```powershell
# UI / 视觉设计规划（Opus 5.5，默认 --mode plan，不允许写代码）
uv run --no-sync python -m scripts.orchestrator run-agy --profile opus --effort high --cwd "<repo-dir>" --prompt "<设计目标与约束>"

# UI / 前端实现（Gemini 4 Neon，允许编辑文件）
uv run --no-sync python -m scripts.orchestrator run-agy --profile neon --effort high --mode accept-edits --cwd "<repo-dir>" --prompt-file "<spec.md>"

# 复杂前端架构或大上下文视觉规划（Gemini 4 Neon XHigh，只读规划）
uv run --no-sync python -m scripts.orchestrator run-agy --profile neon --effort xhigh --mode plan --cwd "<repo-dir>" --prompt "<规划要求>"

# 预览将执行的精确命令（不实际调用）
uv run --no-sync python -m scripts.orchestrator run-agy --dry-run --profile opus --effort high --prompt "design layout"
```

- 当 `--profile opus` 与非 `plan` 模式（如 `accept-edits`、`auto`）组合时，脚本会以退出码 `2` 拦截，提示改用 `--profile neon` 实现；仅在用户明确要求时才允许追加 `--allow-opus-write`。
- 当提示词长度超过 8000 字符时，`run-agy` 自动通过 stdin 传入完整内容，避免触发 Windows 命令行长度限制。

## 原始 CLI 命令速查

| 场景 | 命令模板 |
| --- | --- |
| 查看全部可用模型 | `agy models` |
| 非交互只读规划（Opus 5.5） | `agy -p "<prompt>" --model claude-opus-5-5-high --mode plan --output-format text` |
| 非交互前端实现（Neon） | `agy -p "<prompt>" --model gemini-4-neon-high --mode accept-edits --output-format text` |
| 结构化 JSON 输出 | 增加 `--output-format json`（或 `stream-json`）及 `--json-schema "<schema.json>"` |
| 续接最近或指定会话 | 增加 `--continue` 或 `--conversation "<SESSION_ID>"` |
| 扩展工作区目录 | 增加 `--add-dir "<extra-dir>"`（可重复） |
| 限制单轮超时 | 增加 `--print-timeout 5m` |
| 开启终端沙盒 | 增加 `--sandbox` |

## 实操注意事项

- **两阶段推荐流水线（高审美前端任务）**：
  1. 先用 `run-agy --profile opus --effort high`（自动 `--mode plan`）或 `neon` 产出视觉方案、布局层次、配色与组件拆解；
  2. 再用 `run-agy --profile neon --effort high --mode accept-edits` 依据该方案编写代码，并在需要时由 Codex `run-codex --review` 复核工程质量。
- **内置图像生成**：Antigravity 内置基于 Nano Banana 2 的图像生成工具，可在 Antigravity 会话中直接通过自然语言生成 UI Mockup、图标、插图与架构图；若在 headless CLI 中调用生图工具不可用或受限，可回退到 Codex `$imagegen` 或通过浏览器使用 Gemini / AI Studio。
- **配置与权限**：CLI 配置文件位于 `~/.gemini/antigravity-cli/settings.json`。避免在无隔离环境中随意使用 `--dangerously-skip-permissions`。
- **额度与计费**：Antigravity 与 Gemini、Flow 各自有基础订阅额度，耗尽后可能触发额外购买的 Google AI Credits（见 `https://one.google.com/ai/credits`）。Gemini API 独立计费，不属于 Google AI Pro 订阅额度。
