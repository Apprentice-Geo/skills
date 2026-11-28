# article-format-correction 开发说明

本文件补充 `article-format-correction` 的脚本开发与验证说明；仓库通用的编辑、测试执行环境和提交规范见[根目录开发说明](../AGENTS.md)。

## 职责边界

- `SKILL.md`：Markdown 技术文章校对范围、格式规范、公式与代码处理及检查工作流。
- `scripts/check_format.py`：只读确定性格式检查器，只报告明确可确定的排版与 Markdown 结构问题，不自动改写文件，不做启发式数学公式推断。
- `tests/test_check_format.py`：每条确定性规则的正反例、受保护区域（代码块、行内代码、数学公式、URL、YAML frontmatter）及 CLI 退出码测试。

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

测试执行遵循[根目录测试执行环境规则](../AGENTS.md#测试执行环境)：

```powershell
uv run --no-sync python -m pytest
```
