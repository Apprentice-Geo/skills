# subscription-orchestrator

`subscription-orchestrator` 用于统筹 ChatGPT Plus 与 Google AI Pro 两套订阅能力，帮助 Agent 在 Codex CLI、Antigravity CLI（`agy`）和各项 Web 订阅服务之间做任务路由、跨 Harness 调度与额度降级。

## 核心分工

- **Codex CLI（`gpt-6.2-sol`）**：常规工程编码、重构、调试、测试与独立代码审查。
- **Antigravity CLI — `Claude Opus 5.5`**：仅用于 UI、前端及可视化创意的**设计与规划**（强制只读 `--mode plan`，保护稀缺额度）。
- **Antigravity CLI — `Gemini 4 Neon`**：UI、前端与可视化创意的**编码实现**，并可承担规划任务或在 Codex 不可用时接管工程任务。
- **Web 专项服务**：Google Flow、Flow Music、Gemini Notebook、Deep Research、Google Colab 等，具备浏览器能力的 Harness 可直接通过浏览器操控完成任务。

## 安装与前置条件

- 环境依赖：Windows、Python 3.12、`uv`。
- 订阅 CLI（至少就绪其一，双向调度需两者均已登录）：
  - Codex CLI：已完成 ChatGPT 订阅登录（`codex login status`）。
  - Antigravity CLI（`agy`）：已完成 Google 账号登录（`agy models` 可列出模型）。
- 首次使用前，在本 Skill 目录运行：

```bat
scripts\setup\setup_windows.bat
```

- 探测本机状态：

```powershell
uv run --no-sync python -m scripts.orchestrator probe
```

## 安全与计费边界

- `run-codex` 与 `run-agy` 默认剔除子进程中的 `OPENAI_API_KEY`、`GEMINI_API_KEY` 和 `GOOGLE_API_KEY`，避免订阅限额时意外落到按量计费 API。
- 子进程自动携带 `ORCHESTRATOR_CHILD_HARNESS=1` 与防递归提示词，防止两个 Harness 互相嵌套调用。
- 检测到额度不足时返回退出码 `3`，Agent 立即停用对应 Harness 并降级。

## 进一步阅读

- [SKILL.md](SKILL.md)：完整路由表、脚本用法、交接规范与降级策略。
- [references/codex-cli.md](references/codex-cli.md)：Codex CLI 实测参数、模型配置、代码审查与 `$imagegen` 用法。
- [references/antigravity-cli.md](references/antigravity-cli.md)：Antigravity CLI（`agy`）实测模型 ID、模式约束与进阶参数。
- [references/subscription-catalog.md](references/subscription-catalog.md)：ChatGPT Plus 与 Google AI Pro 专项 Web 服务速查及额度管理入口。
