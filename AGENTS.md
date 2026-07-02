# Repository Guide

This repository stores personal-use agent skills (个人使用 skills). These skills are not neutral generic templates; they encode strong personal preferences (较强个人偏好) about how agents should collaborate, edit, review, and verify work.

## Repository Definition

- Treat this repository as a personal skill collection (个人 skill 集合), not a general-purpose public standard library.
- Skills should capture behavior the agent may not know by default: personal workflows, recurring corrections, preferred boundaries, and failure modes observed in practice.
- Avoid over-explaining common knowledge unless the user has explicitly added it because agents repeatedly get it wrong.
- Broad triggering can be acceptable for personal-use skills when the user wants that behavior to apply often.

## Writing Style

- Write skill bodies in English.
- Add Chinese only as parenthetical annotations next to the corresponding English term, for example `plan-first mode (先讨论计划)`.
- Keep instructions concise and operational. Prefer specific behavior rules over long background explanation.

## Editing Rules

- Preserve unrelated local edits and untracked files.
- Do not rewrite a skill into a generic best-practices document if it is meant to preserve the user's personal workflow.
- When changing one skill, do not modify other skills unless the user explicitly asks.
- For documentation-only edits (仅文档修改), avoid changing runtime code, scripts, tests, or dependencies.

## Current Skill Notes

- `coding-guidelines` is the baseline coding-collaboration skill. It may be combined with more specific skills when a task also matches a domain workflow.
- `article-format-correction` is a light Markdown correction skill. It should preserve the author's meaning, structure, expression style, and explicit edit scope.
- `anti-sycophancy-review` is a rigorous review skill. It should challenge unsupported assumptions without turning every answer into hostile disagreement or unreadable tagging.
