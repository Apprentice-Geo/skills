# coding-guidelines 开发说明

本文件补充 `coding-guidelines` 的维护约定与刻意保留的约束；仓库通用的编辑和提交规范见[根目录开发说明](../AGENTS.md)。

## 文档职责

- `SKILL.md`：编码、审查、缺陷诊断、文档编写、验证和结果汇报的通用准则。
- `references/DOCUMENTATION.md`：常驻文档、工作文档与归档文档的组织和维护规则。
- `references/DOCUMENT-ROLES.md`：常见文档的读者、职责与内容范围参考。
- `references/FRONTEND.md`：前端界面开发、布局与动画验证准则。

## 刻意约束

- **界面文案与演示稿排除过程信息**：`SKILL.md` 要求界面文案和演示稿不包含曾用方案、设计取舍和变更经过（仅排除这三类过程信息，不排除读者或观众需要理解的当前技术结论或实现内容）。这是仓库所有者刻意设置的约束，审查或修订本 Skill 时不要将其当作与「按读者需要组织内容」矛盾的缺陷。

## 验证

修改文档后，在仓库根目录运行 `uv run --script .github/scripts/check_docs.py` 和 `git diff --check`。
