# Apprentice-Geo/skills

This repository contains Agent Skills for personal use. The Skills reflect personal workflows, writing preferences, responsibility boundaries, and lessons learned from practice.

中文版: [README.md](README.md)

## Skill List

| Name | Purpose |
| --- | --- |
| `audio-transcribe` | Transcribe local audio into reusable artifacts with faster-whisper or Qwen3-ASR. |
| `bili-audiosummary` | Prepare and resume Bilibili audio-summary jobs using native subtitles or external transcription artifacts. |
| `subtitle-creator` | Create JSON or SRT subtitles from local audio with optional text correction. |
| `article-format-correction` | Correct the Markdown formatting of Markdown articles while preserving meaning, structure, and the author's expressive style. |
| `anti-sycophancy-review` | Review claims, plans, predictions, and interpretations without unsupported agreement. |
| `coding-guidelines` | Develop, review, and debug code, assess proposed changes, organize project documentation and handoff notes, and write documentation, UI copy, and presentations for their intended readers. |
| `subscription-orchestrator` | Orchestrate ChatGPT Plus and Google AI Pro subscription resources across Codex CLI, Antigravity CLI, and Web services with model routing, quota guards, and cross-harness delegation. |

## Usage and Environment

Copy any Skill directory into your Agent client's skills folder to use it independently; Skills do not depend on sibling directory layout.

- Instruction-only Skills (`anti-sycophancy-review`, `coding-guidelines`) require no runtime dependencies.
- Script-backed Skills (`audio-transcribe`, `bili-audiosummary`, `subtitle-creator`, `article-format-correction`, `subscription-orchestrator`) use Python 3.12 and `uv`, with Windows `.bat` setup launchers. See each Skill's `SKILL.md` or `README.md` for details.
- Documentation and link check: `uv run --script .github/scripts/check_docs.py`.
