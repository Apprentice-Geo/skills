---
name: article-format-correction
description: Corrects Markdown technical articles, notes, algorithm writeups, and debugging records by fixing obvious writing, punctuation, Markdown formatting, and formula markup errors while preserving the author's meaning, structure, and personal technical-blog style. Use when the user asks to revise, correct, format, proofread, or polish only clear errors in Markdown articles.
license: Apache-2.0
---

# article-format-correction

Correct Markdown technical articles with a light hand.

This skill fixes clear errors in writing, punctuation, Markdown syntax, and formula markup. It must not rewrite the article into a textbook, official document, marketing article, or beginner tutorial.

The corrected article should still feel like the author's own technical blog: practical, direct, record-oriented, and based on the original problem-solving path.

## Capability Boundaries

The skill is designed for:

- 修正错误、校对文章、修改格式
- 修正 Markdown、公式、标题、列表、代码块周边格式
- 校订技术博客、调试记录、配置记录、算法题解、技术笔记

Do not use this skill for translation, summarization, expansion, heavy rewriting, article generation, or modify articles not written in Chinese or English.

## Editing Contract

Preserve:

- original meaning and technical conclusions
- original section order and explanation order
- original heading hierarchy unless clearly malformed
- original paragraph boundaries unless Markdown syntax is clearly broken
- code logic, commands, paths, URLs, logs, version numbers, and formulas
- the author's uncertainty level and personal record tone

Do not add facts, examples, explanations, conclusions, or background knowledge.

## Preserve Author Style

Apply this personal styles as a preservation guide, not as a reason to rewrite.

Keep these traits:

- Medium-high information density: direct conclusion, steps, and necessary explanation.
- Short to medium sentences, with longer causal sentences only where the original already uses them.
- Practice-first organization: background/problem -> attempt -> error/phenomenon -> cause judgment -> final solution -> summary.
- Algorithm organization: problem -> core idea -> mapping/derivation -> code -> complexity or optimization.
- Direct technical terms such as `Dijkstra`, `python` , `Playwright`, `Electron` and similar domain terms.
- Personal technical-blog tone.
- Real-world details: commands, paths, filenames, screenshots, logs, versions, complete snippets, and concrete error messages.

Do not make uncertain judgments sound certain. 

Do not soften direct conclusions without reason. 

Do not replace the author's problem-solving order with a knowledge-system order.

## Safe Corrections

Correct only clear issues:

- typos, duplicated characters, and obvious grammar mistakes
- missing or incorrect punctuation
- Chinese-English and Chinese-number spacing when it improves standard technical writing
- missing spaces after Markdown heading markers
- malformed headings, lists, tables, links, images, or fences when the intended Markdown is obvious
- formula markup errors
- unmarked mathematical expressions that should be LaTeX
- using Chinese symbols for Chinese sentences
- using English symbols for English sentences, codes and formulas

Symbols examples:

- Chinese： `，。、：`
- English: `,.:`

**Exceptionally**, use `「」[]` and ` ` (half-width space)  instead of `“”【】` and `　` (full width space) for Chinese sentences.

For formulas or codes:

- Keep valid existing LaTeX and code block unchanged.
- Use "$...$" for short inline formulas.
- Use "$$...$$" for standalone formulas.
- Use "`...`" for short inline code block.
- Use "```...```" with correct language for standalone code block.
- Use english half-width symbol instead of chinese full width symbol.
- Preserve variables, notation, and mathematical meaning.
- Do not derive, simplify, or reinterpret formulas or codes.

Example:

```text
时间复杂度为 O(n log n)
└--> 时间复杂度为 $O \left (n \log n \right )$
zip（）返回一个包含一些元组的迭代器
└--> `zip()` 返回一个包含一些元组的迭代器
统计满足x*y=k的x，y数量
└--> 统计满足 $x \times y = k$ 的数对 $x,y$ 数量
统计满足x《=y《=z的数对（x，y，z）数量
└--> 统计满足 $x \le y \le z$ 的数对 $\left ( x,y,z \right )$ 数量
递推公式为dpi=dpi-1+dpi-2，i》=2
└--> 递推公式为 $dp_i = dp_{i-1} + dp_{i-2}, i \ge 2$
```

## Output

Return the corrected article in the original file.

Tell the user revision note using this structure:

```markdown
**修改说明**

- 简短说明修改了哪些类型的问题。
- 只列出有意义的修改，不逐个列出所有标点和空格调整。

**需确认项**

- 如果没有需确认项，写“无”。
```

Good summary items:

- 修正错别字。
- 修正 Markdown 标题或列表格式。
- 调整中英文间空格。
- 将未标记公式改为 LaTeX 公式。
- 保留疑似术语并列入需确认项。

## Protected Content

Do not modify these unless there is an explicit formatting error:

- fenced code blocks and inline code
- command lines, terminal output, error logs
- file paths, URLs, Markdown links, image links
- Markdown tables
- version numbers and project-specific names
- existing valid LaTeX formulas
- technical terms whose correctness is uncertain

If protected content looks suspicious but is not clearly wrong, keep it unchanged and list it under `需确认项`.

## Uncertainty

When a term, command, path, version, formula, or technical statement may be wrong but cannot be confidently corrected:

1. Keep the original text unchanged.
2. Add it to "**需确认项**" in the revision note.
3. Briefly explain why it needs confirmation.

Do not guess.

## Final Check

Before finishing, verify:

- Markdown is still valid.
- Code blocks, inline code, URLs, logs, tables, and valid formulas were not changed accidentally.
- No facts or conclusions were added.
- No technical conclusion changed.
- The article still reads like the author's personal blog, not formal documentation.
