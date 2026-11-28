from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_format import check_text, main


def test_clean_document_passes() -> None:
    doc = """---
title: 示例文章
---

# 使用 Python 进行测试

在 2026 年，我们在 GitHub 上使用 Markdown 记录了 15% 的提升和 90° 的旋转。

中文句子使用「引号」和『嵌套引号』，以及 [方括号]。

参考 [示例](<https://example.com/a b.png>) 和图片 ![图](<./fig 1.png>)。

时间复杂度为 $O(n \\log n)$，展开如下：

$$
dp_i = dp_{i-1} + dp_{i-2}
$$

| 列一 | 列二 |
| --- | --- |
| 值 1 | `a|b` |

```python
# 代码块内不做排版检查: 使用Python和“引号”和$ x $
url = "https://github.com/example"
```

[ref]: <https://example.com>
"""
    assert check_text(doc) == []


def test_space_cjk_latin_and_degree_percent() -> None:
    issues = check_text("使用Python处理100个文件，提升 15 % 和 90 °。")
    rules = [i.rule for i in issues]
    assert rules.count("space-cjk-latin") == 4
    assert rules.count("no-space-degree-percent") == 2


def test_fullwidth_and_forbidden_punctuation() -> None:
    issues = check_text("这里有“引号”、【括号】、全角\u3000空格， 以及多余空格 。")
    rules = [i.rule for i in issues]
    assert rules.count("forbidden-punctuation") == 5
    assert rules.count("no-space-fullwidth") == 2


def test_halfwidth_punct_in_cjk() -> None:
    issues = check_text("第一项,第二项;第三项!还有(括号)和英文句 Hello, world.")
    rules = [i.rule for i in issues]
    assert rules.count("halfwidth-punct-in-cjk") == 5


def test_link_angle_brackets_inline_image_and_ref() -> None:
    doc = """[示例](https://example.com)
![图](./a b.png)
[ref]: https://example.com/ref
"""
    issues = check_text(doc)
    assert [i.rule for i in issues] == [
        "link-angle-brackets",
        "link-angle-brackets",
        "link-angle-brackets",
    ]


def test_math_delimiter_space_and_currency_exemption() -> None:
    doc = "公式 $ x + 1 $ 有空格，但价格 $100 和 $200 不是公式，正确公式是 $x+1$。"
    issues = check_text(doc)
    assert len(issues) == 1
    assert issues[0].rule == "math-delimiter-space"


def test_proper_nouns_and_unidiomatic_abbr() -> None:
    doc = "我在 github 上用 typescript 和 Ts 以及 h5 写 markdown。"
    issues = check_text(doc)
    by_rule = [(i.rule, i.severity, i.snippet) for i in issues]
    assert ("proper-noun-case", "error", "github") in by_rule
    assert ("proper-noun-case", "error", "typescript") in by_rule
    assert ("proper-noun-case", "error", "markdown") in by_rule
    assert ("unidiomatic-abbr", "warning", "Ts") in by_rule
    assert ("unidiomatic-abbr", "warning", "h5") in by_rule


def test_unclosed_code_fence_and_table_column_mismatch() -> None:
    doc = """| A | B |
| --- | --- |
| 1 | 2 | 3 |

```python
print("oops")
"""
    issues = check_text(doc)
    rules = [i.rule for i in issues]
    assert "table-column-mismatch" in rules
    assert "unclosed-code-fence" in rules


def test_protected_regions_ignored() -> None:
    doc = """---
note: 使用Python“引号”
---

行内代码 `使用Python和“引号”和[a](b)` 不报。
裸链接 https://github.com/user/repo 不报。
HTML 标签 <span class="github">内容</span> 中标签属性不报。
"""
    assert check_text(doc) == []


def test_cli_exit_codes_and_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ok_file = tmp_path / "ok.md"
    ok_file.write_text("使用 Python 的 `github` 和 h5。\n", encoding="utf-8")

    bad_file = tmp_path / "bad.md"
    bad_file.write_text("使用Python。\n", encoding="utf-8")

    # Only warning -> exit code 0
    assert main([str(ok_file), "--json"]) == 0
    # Error -> exit code 1
    assert main([str(bad_file)]) == 1
    # Missing file -> exit code 2
    assert main([str(tmp_path / "missing.md")]) == 2

    # Verify JSON payload structure on bad_file
    capsys.readouterr()
    assert main([str(bad_file), "--json"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["errors"] == 1
    assert payload["issues"][0]["rule"] == "space-cjk-latin"
