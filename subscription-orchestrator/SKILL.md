---
name: subscription-orchestrator
description: 统筹利用 ChatGPT Plus 与 Google AI Pro 订阅资源，指导 Agent 按任务类型在 Codex CLI（gpt-6.2-sol）、Antigravity CLI（Gemini 4 Neon / Claude Opus 5.5）及订阅 Web 服务间进行跨 Harness 调度、思考强度选择、浏览器自动化与额度降级。适用于需要跨模型分工、调用另一侧 Harness 编码/审查/前端设计，或使用订阅内图像、视频、音乐、Notebook、Deep Research 与 Colab 能力的场景。
license: Apache-2.0
metadata:
  Github: https://github.com/Apprentice-Geo/skills/tree/main/subscription-orchestrator
---

# subscription-orchestrator

本 Skill 用于将任务路由到最合适的订阅 Harness、模型或 Web 服务，并在额度不足时安全降级。

## 任务路由与模型策略

| 任务类型 | 首选 Harness / 资源 | 模型与模式 | 说明 |
| --- | --- | --- | --- |
| 常规工程编码、重构、调试、单测 | Codex CLI | `gpt-6.2-sol`（写入用 `workspace-write`） | 工程开发主力；已在 Codex 内时直接执行 |
| 独立代码审查 / 交叉验证 | 非主写方 Harness | Codex `exec review`（`gpt-6.2-sol`）或 Antigravity `Neon`（`plan`） | 由未参与编写的一侧执行独立复核 |
| UI、前端、可视化创意的**设计/规划（不编码）** | Antigravity (`agy`) | `Claude Opus 5.5`（`--mode plan`）或 `Gemini 4 Neon` | Opus 5.5 额度极少，严格只用于不写代码的设计与规划 |
| UI、前端、可视化创意的**编码实现** | Antigravity (`agy`) | `Gemini 4 Neon`（`--mode accept-edits`） | Opus 5.5 与 Neon 的前端可视化水平均高于 GPT；Neon 亦可兼做规划 |
| 图像生成与编辑 | 当前可用 Harness 内置能力 | Antigravity 内置生图（Nano Banana 2）或 Codex `$imagegen` | 优先使用可直出本地文件的订阅内能力 |
| 视频、音乐/BGM、播客、深度研究、GPU、文档 | 订阅 Web 服务 | 见[订阅资源速查](references/subscription-catalog.md) | 任务涉及非编码专项资源时按需查阅 |

路由约束：

- **避免无谓调度**：单文件小改、简单只读查询，或当前所处 Harness 本身就是首选路由时，直接在当前 Harness 完成。
- **严禁递归套娃**：若环境存在 `ORCHESTRATOR_CHILD_HARNESS=1` 或提示词包含 `[Orchestrator Guard]`，不得再调用 `codex`、`agy` 或本 Skill 的 `run-*` 子命令。
- **思考强度按复杂度选择**：
  - `low` / `medium`：明确小改、常规格式化、简单问答或轻量规划。
  - `high`（默认）：常规多文件实现、代码审查、UI/前端设计与实现。
  - `xhigh`（Codex 亦支持 `max` / `ultra`）：复杂排错、核心架构决策、高风险跨模块重构。`Claude Opus 5.5` 最高支持 `high`，`Gemini 4 Neon` 最高支持 `xhigh`。

## 探测与日常调用脚本

本 Skill 提供轻量封装脚本（仅用 Python 标准库），用于探测本机 CLI 可用性、统一参数约定、注入防递归护栏、强制 Opus 5.5 只读规划约束、屏蔽易触发独立计费的 API Key 环境变量，并统一识别限额退出码。

首次使用前，在 Windows 下从本 Skill 目录运行 `scripts\setup\setup_windows.bat` 配置 Python 3.12 环境（需要 `uv`）。

### 1. 环境探测

跨 Harness 调用前先运行探测，确认另一侧 CLI 已安装、已认证且目标模型可用：

```powershell
uv run --no-sync python -m scripts.orchestrator probe
```

- 加 `--format json` 输出结构化 JSON；加 `--fast` 跳过 `agy models` 联网查询。
- 脚本除检索 `PATH` 外，还会自动回退查找 `%LOCALAPPDATA%\agy\bin\agy.exe` 与 Codex 默认安装路径，避免桌面端长生命周期进程 `PATH` 未刷新导致误报未安装。

### 2. 调用另一侧 Harness

常规工程编码或代码审查（委托给 Codex `gpt-6.2-sol`）：

```powershell
# 独立代码审查（默认只读、gpt-6.2-sol、high）
uv run --no-sync python -m scripts.orchestrator run-codex --review --uncommitted --cwd "<repo-dir>"

# 工程实现（显式开启 workspace-write）
uv run --no-sync python -m scripts.orchestrator run-codex --sandbox workspace-write --effort high --cwd "<repo-dir>" --prompt "<自包含任务说明>"
```

UI/前端规划与实现（委托给 Antigravity `agy`）：

```powershell
# UI / 视觉设计规划（Opus 5.5，强制 --mode plan，不写代码）
uv run --no-sync python -m scripts.orchestrator run-agy --profile opus --effort high --cwd "<repo-dir>" --prompt "<设计规划要求>"

# UI / 前端编码实现（Gemini 4 Neon）
uv run --no-sync python -m scripts.orchestrator run-agy --profile neon --effort high --mode accept-edits --cwd "<repo-dir>" --prompt "<实现要求>"
```

- 长提示词或含多行代码块时，使用 `--prompt-file <path>` 或 `--prompt -`（stdin）传入，避免 PowerShell 转义问题。
- 详细 CLI 参数、原始命令及 `$imagegen`、会话续接等进阶用法，按需查阅：[Codex CLI 参考](references/codex-cli.md)、[Antigravity CLI 参考](references/antigravity-cli.md)。

### 3. 退出码约定

- `0`：执行成功（`probe` 表示至少有一个 Harness 就绪）。
- `1`：子进程执行失败或超时（非限额原因）。
- `2`：参数错误、CLI 未找到、递归调用拦截或策略拦截（如试图让 Opus 5.5 以写入模式运行）。
- `3`：检测到额度不足或速率限制（`QUOTA_EXHAUSTED`）。

## 跨 Harness 交接要求

- **自包含交接**：子 Harness 看不到父会话历史。提示词必须写清目标、涉及文件路径、项目约束与验收标准，避免只写“按上面说的改”。
- **权限与并发隔离**：只读调研与审查保持只读模式；允许写代码时明确修改范围，不要让两个 Harness 并发编辑同一批文件。
- **结果验收**：子 Harness 返回后，由父 Agent 核对变更、测试或审查结论，再统一向用户汇报。

## Web 专项资源与浏览器操控

当任务需要视频生成、配乐、Notebook 多模态产物、Deep Research 或 Colab 等 Web 专属能力时，先阅读[订阅资源速查](references/subscription-catalog.md)选择入口。

- **允许浏览器操控**：当前 Harness 具备浏览器能力（如 Antigravity）且用户未要求手动操作时，可直接通过浏览器访问并操作对应订阅 Web 服务完成任务并取回产物。
- **Credits 保护**：在 Google Flow 等消耗独立月度 Credits 的页面发起生成前，先核对页面显示的剩余订阅额度；订阅额度不足或会触发额外付费 Google AI Credits 自动消耗时，停止并向用户报告。
- **阻塞即停**：遇到账户未登录、地区受限、人机验证（CAPTCHA）或付费确认页面时立即停止并请用户接管，不得尝试绕过。

## 额度不足与降级规则

- 任一 Harness 返回退出码 `3`、限额报错或探测状态非 `ready` 时，**本轮任务内不再继续调用该 Harness**，不盲目重试。
- 降级顺序：
  - `Claude Opus 5.5` 额度不足 → 改用 `Gemini 4 Neon`（`--profile neon --mode plan`）做规划与设计；
  - Antigravity 整体额度不足 → 前端/设计任务降级回 Codex（在汇报中说明视觉方案可能需人工微调）或提示用户改用对应 Web 入口；
  - Codex 额度不足 → 工程开发与审查降级回 Antigravity `Gemini 4 Neon`（避免为常规编码消耗 Opus 5.5）。
- **严禁静默切付费 API**：不得在订阅额度不足时使用 `OPENAI_API_KEY`、`GEMINI_API_KEY` 或 `GOOGLE_API_KEY` 走独立计费接口，也不得未经确认启用额外付费 Credits。
- 发生降级时，向用户明确说明原因、替代执行方及潜在差异。
