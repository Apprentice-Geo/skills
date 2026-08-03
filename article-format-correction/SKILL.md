---
name: article-format-correction
description: Corrects Markdown technical articles(技术博客), notes, algorithm writeups(算法题解), and debugging records(调试记录) by fixing obvious writing, punctuation, Markdown formatting(格式调整), and formula markup errors while preserving the author's meaning, structure, and expression style. Use when the user asks to revise(修正), correct, format, or proofread(校对) in Markdown articles.
license: Apache-2.0
---

# article-format-correction

Correct Markdown technical articles with a light hand. Fix clear writing, punctuation, Markdown, and formula-markup errors while applying the personal formatting preferences below.

## Scope and Priority

Use this skill for proofreading and formatting Chinese or English technical blogs, notes, debugging records, configuration notes, and algorithm writeups. Do not use it for translation, summarization, expansion, heavy rewriting, article generation, or knowledge-system reorganization.

Use light correction mode by default: fix only errors that are clear from the text itself. User instructions take precedence over this skill. User-specified ranges and "do not modify the rest" instructions are hard boundaries.

Unless the user explicitly asks for a broader rewrite, do not add facts, examples, explanations, conclusions, or background knowledge; change the author's meaning, technical conclusions, section order, explanation order, paragraph boundaries, tone, wording habits, or uncertainty level; or replace informal notes with formal documentation language.

## Safe Corrections and Formatting Preferences

Correct only clear issues:

- Typos, duplicated characters, obvious grammar mistakes, and missing or incorrect punctuation.
- Chinese-English and Chinese-number spacing when it improves standard technical writing.
- Malformed headings, lists, tables, links, images, or fences when the intended Markdown is obvious.
- Missing spaces after Markdown heading markers.
- Formula markup errors and unmarked mathematical expressions that should be LaTeX.
- Chinese symbols used in English sentences, code, or formulas, and English symbols used in Chinese sentences.

Apply these personal preferences unless the user specifies otherwise:

- Use Chinese punctuation in Chinese sentences and English half-width punctuation in English, code, and formulas.
- Exceptionally, use `「」[]` and a half-width space instead of `“”【】` and a full-width space in Chinese sentences.
- Put a space between bold markers and surrounding text, but not between the markers and their content: `这是 **重点内容**。`.
- Put angle brackets around every Markdown link destination, including image links and reference definitions: `[示例](<https://example.com>)`.

## Formulas and Code

- Keep valid existing LaTeX unchanged.
- Use `$...$` for short inline formulas and `$$...$$` for long or large standalone formulas; never put spaces between `$` and formula content.
- Preserve variables, notation, and mathematical meaning. Do not derive, simplify, or reinterpret formulas or code.
- Rewrite text with clear mathematical meaning as LaTeX, including dynamic-programming recurrences, variable ranges, sums, products, set operations, and simple assignments such as `N = duration_samples`.
- Convert formula-like fenced blocks and inline code when they clearly express mathematics. When an identifier contains an underscore, preserve it as text: `$N = \text{duration\_samples}$`.
- Use `` `...` `` for short inline code and fenced code blocks with the correct language for genuine standalone code.
- Keep genuine program code, commands, logs, and other non-mathematical code unchanged.

Examples:

| Before | After | Rule |
| --- | --- | --- |
| ``时间复杂度为 O(n log n)`` | ``时间复杂度为 $O(n \log n)$`` | Rewrite unmarked mathematical expressions as LaTeX. |
| ``统计满足 x\*y=k 的 x，y 数量`` | ``统计满足 $x \times y = k$ 的数对 $x, y$ 数量`` | Rewrite mathematical expressions as LaTeX. |
| ``n、in【1，2e5】`` | ``$n \in [1,2 \times 10^5]$`` | Rewrite mathematical expressions as LaTeX. |
| ``递推公式为 dpi=dpi-1+dpi-2，i>=2`` | ``递推公式为 $dp_i = dp_{i-1} + dp_{i-2}, i \ge 2$`` | Rewrite recurrences as LaTeX. |
| ``N = duration_samples`` | ``$N = \text{duration\_samples}$`` | Rewrite formula-like assignments as LaTeX. |
| ``N ∈ dp[K][j]`` | ``$N \in dp[K][j]$`` | Rewrite set-membership expressions while preserving array-access notation. |
| ``score = Σ loadᵢ²`` | ``$score = \sum_i load_i^2$`` | Rewrite summation expressions without inventing missing bounds. |
| ``zip（）返回一个包含一些元组的迭代器`` | `` `zip()` 返回一个包含一些元组的迭代器 `` | Use inline code for code identifiers. |
| ``[示例](./example image.png)`` | ``[示例](<./example image.png>)`` | Put angle brackets around link destinations. |

For a long or large formula, use an independent display block:

```markdown
$$
dp_i = \sum_{j=0}^{i-1} dp_j \times w_{j,i}, \quad i \ge 1
$$
```

## Protected Content and Uncertainty

Unless there is an explicit formatting error, do not modify:

- Genuine fenced code blocks, genuine inline code, commands, terminal output, or error logs.
- File paths and URL contents. Markdown link and image destinations still follow the angle-bracket rule.
- Markdown table structure. Apply formatting rules to ordinary text inside table cells.
- Version numbers, project-specific names, existing valid LaTeX, and technical terms whose correctness is uncertain.

Do not reformat genuine code, change indentation, normalize code style, or rewrite comments unless the user explicitly asks. A fenced block or inline code span that clearly expresses a mathematical formula is an exception and must be converted according to the formula rules.

If a term, command, path, version, formula, or technical statement may be wrong but cannot be confidently corrected, keep it unchanged, do not guess, and list it under `需确认项` with a brief reason.

## Final Check

Before finishing, verify:

- Markdown is valid; code-fence count and boundaries are correct.
- Markdown tables retain their row and separator structure.
- Genuine code, commands, paths, URLs, logs, tables, and valid formulas were not changed accidentally; formula-like code was converted intentionally.
- Formatting is consistent: unless an explicit exception applies, the same kind of object uses the same format throughout the article.
- Commands, file paths, version numbers, and project-specific names were not normalized or rewritten accidentally.
- No facts were added and no technical conclusion changed.
- The article still reads like the author's personal blog, not formal documentation.

## Output

If editing a file, update the original file. If the user pasted text directly, return the corrected text.

Keep the revision note concise and do not list every punctuation, spacing, or formatting change.

Use this structure:

```markdown
**修改说明**

- 简短说明有意义的修改类型。

**需确认项**

- 无
```
