---
name: article-format-correction
description: Corrects Markdown technical articles(技术博客), notes, algorithm writeups(算法题解), and debugging records(调试记录) by fixing obvious writing, punctuation, Markdown formatting(格式调整), and formula markup errors while preserving the author's meaning, structure, and expression style. Use when the user asks to revise(修正), correct, format, proofread(校对), or polish clear errors(润色) in Markdown articles.
license: Apache-2.0
---

# article-format-correction

Correct Markdown technical articles with a light hand.

This skill fixes clear errors in writing, punctuation, Markdown syntax, and formula markup. It must not rewrite the article into a textbook, official document, marketing article, beginner tutorial, or knowledge-base article.

The corrected article should preserve the author's expression style. Do not improve, normalize, or rewrite style beyond clear error correction.

## Default Editing Mode

Use light correction mode (轻量校对) by default. Fix only clear errors that are visible from the text itself.

If the user specifies a heading, section, paragraph, range, or "do not modify the rest" (其余不用改动), edit only that scope. Treat explicit scope limits as hard boundaries.

Do not expand, summarize, translate, add examples, add background knowledge, or convert the article into formal documentation unless the user explicitly asks for that kind of rewrite.

## Capability Boundaries

The skill is designed for:

- fixing clear errors, proofreading articles, and adjusting formatting
- fixing Markdown, formulas, headings, lists, and formatting around code fences
- proofreading technical blogs, debugging records, configuration notes, algorithm writeups, and technical notes

Do not use this skill for translation, summarization, expansion, heavy rewriting, article generation, or modifying articles not written in Chinese or English.

## Editing Contract

Preserve:

- original meaning and technical conclusions
- original section order and explanation order
- original heading hierarchy unless clearly malformed
- original paragraph boundaries unless Markdown syntax is clearly broken
- code logic, commands, paths, URLs, logs, version numbers, and formulas
- the author's uncertainty level, wording habits, and original tone
- user-specified edit scope, including section-only edits and "do not modify the rest" (其余不用改动)

Do not add facts, examples, explanations, conclusions, or background knowledge.

## Preserve Expression Style

Correct only clear errors. Do not improve the article's expression style.

Do not rewrite sentences that are understandable but stylistically imperfect.

Do not replace informal notes with formal documentation language.

Do not reorder the author's explanation, problem-solving path, or argument structure.

Do not normalize terms, examples, or wording choices just because another style would be more polished.

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
- Use English half-width punctuation instead of Chinese full-width punctuation.
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

If editing a file, update the original file. If the user pasted text directly, return the corrected text.

Keep the revision note concise. Do not list every punctuation, spacing, or formatting change one by one.

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

Do not reformat code inside fenced code blocks, change indentation, normalize code style, or rewrite comments unless the user explicitly asks or the fenced block itself has a clear Markdown formatting error.

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
- Code fence count and fence boundaries are still correct.
- Markdown tables still keep their row and separator structure.
- Code blocks, inline code, URLs, logs, tables, and valid formulas were not changed accidentally.
- Commands, file paths, version numbers, and project-specific names were not normalized or rewritten accidentally.
- No facts or conclusions were added.
- No technical conclusion changed.
- The article still reads like the author's personal blog, not formal documentation.
