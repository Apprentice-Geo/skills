# Apprentice-Geo/skills

本仓库是个人使用的 Agent Skill 合集，仓库中的 Skill 会带有明显的个人工作流、表达偏好、职责边界和实践经验。

English version: [README.en.md](README.en.md)

## Skill 列表

| 名称 | 用途 |
| --- | --- |
| `audio-transcribe` | 使用 faster-whisper 或 Qwen3-ASR 将本地音频转写为可复用的转写产物。 |
| `bili-audiosummary` | 基于原生字幕或外部转写产物，准备并恢复 Bilibili 音频总结任务。 |
| `subtitle-creator` | 从本地音频创建 JSON／SRT 字幕，并可选地校正文本。 |
| `article-format-correction` | 在保留含义、结构和作者表达风格的前提下，校正 Markdown 文章格式。 |
| `anti-sycophancy-review` | 审查论点、计划、预测和解释，避免无依据地附和。 |
| `coding-guidelines` | 指导代码开发、审查、诊断与方案评估，组织项目文档和交接记录，并按读者需要编写文档、界面文案和演示稿。 |
| `subscription-orchestrator` | 统筹 ChatGPT Plus 与 Google AI Pro 订阅资源，在 Codex CLI、Antigravity CLI 与订阅 Web 服务间进行任务路由、跨 Harness 调度与额度降级。 |

## 使用与环境

将需要的 Skill 目录放到 Agent 客户端的 skills 目录即可独立使用，各 Skill 之间不依赖相邻目录关系。

- 纯指令型 Skill（`anti-sycophancy-review`、`coding-guidelines`）无额外运行依赖。
- 带脚本的 Skill（`audio-transcribe`、`bili-audiosummary`、`subtitle-creator`、`article-format-correction`、`subscription-orchestrator`）使用 Python 3.12 和 `uv` 管理依赖，提供 Windows `.bat` 环境配置脚本；具体环境要求见各 Skill 的 `SKILL.md` 或 `README.md`。
- 文档结构与链接检查：`uv run --script .github/scripts/check_docs.py`。
