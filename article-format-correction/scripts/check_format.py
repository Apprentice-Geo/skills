from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    path: str
    line: int
    column: int
    severity: Severity
    rule: str
    message: str
    snippet: str


CJK_CHAR = r"\u4e00-\u9fff\u3400-\u4dbf"
CJK_RE = re.compile(f"[{CJK_CHAR}]")

# Rules checked on prose text (after masking code, math, links/URLs, HTML).
SPACE_CJK_LATIN_RE = re.compile(
    rf"(?P<left>[{CJK_CHAR}])(?P<right>[A-Za-z0-9])"
    rf"|(?P<left2>[A-Za-z0-9])(?P<right2>[{CJK_CHAR}])"
)
DEGREE_PERCENT_SPACE_RE = re.compile(r"(\d)\s+([°%])")
FULLWIDTH_PUNCT = "，。！？；：、（）《》「」『』"
FULLWIDTH_SPACE_BEFORE_RE = re.compile(rf"(?<=\S)[ \t]+([{re.escape(FULLWIDTH_PUNCT)}])")
FULLWIDTH_SPACE_AFTER_RE = re.compile(rf"([{re.escape(FULLWIDTH_PUNCT)}])[ \t]+(?=\S)")

FORBIDDEN_PUNCT_MAP: dict[str, str] = {
    "\u201c": "「",
    "\u201d": "」",
    "\u2018": "『",
    "\u2019": "』",
    "【": "[",
    "】": "]",
    "\u3000": " ",
}
FORBIDDEN_PUNCT_RE = re.compile("[" + "".join(re.escape(k) for k in FORBIDDEN_PUNCT_MAP) + "]")

# Half-width punctuation directly adjacent to CJK characters (conservative to avoid false positives).
HALFWIDTH_PUNCT_IN_CJK_RE = re.compile(rf"(?<=[{CJK_CHAR}])([,;!?()])|([,;!?()])(?=[{CJK_CHAR}])")

# Inline link / image: [text](target) or ![alt](target)
INLINE_LINK_RE = re.compile(r"(?P<prefix>!?\[(?:[^\]\\]|\\.)*\])\((?P<inner>[^)\n]+)\)")
# Reference link definition: [label]: target
REF_DEF_RE = re.compile(r"^[ ]{0,3}\[(?P<label>[^\]\n]+)\]:[ \t]*(?P<target>\S+)(?:[ \t]+.*)?$")

# Inline math: single $ ... $ (not $$ ... $$ and not escaped \$).
INLINE_MATH_RE = re.compile(r"(?<!\\)(?<!\$)\$(?!\$)(?P<body>[^$\n]+?)(?<!\\)\$(?!\$)")

# URLs & HTML tags to mask before prose checks.
URL_RE = re.compile(r"https?://[^\s<>)\]]+")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*)?/?>")

PROPER_NOUNS: dict[str, str] = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "chatgpt": "ChatGPT",
    "openai": "OpenAI",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "markdown": "Markdown",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "macos": "macOS",
    "ios": "iOS",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "vscode": "VS Code",
    "bilibili": "Bilibili",
    "youtube": "YouTube",
    "google": "Google",
    "python": "Python",
}

# Only flag clearly wrong casing forms when the exact token is not already canonical.
PROPER_NOUN_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z][A-Za-z0-9.]*)(?![A-Za-z0-9_./-])")

UNIDIOMATIC_ABBR: dict[str, tuple[str, Severity]] = {
    "Ts": ("TypeScript", "warning"),
    "h5": ("HTML5", "warning"),
    "H5": ("HTML5", "warning"),
}

TABLE_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _frontmatter_end(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return idx + 1
    return 0


def _collect_fenced_lines(
    lines: list[str], offset: int, display_path: str
) -> tuple[set[int], list[Issue]]:
    """Return 1-based line numbers inside fenced code blocks and any unclosed fence issues."""
    parser = MarkdownIt()
    body = lines[offset:]
    fenced_lines: set[int] = set()
    issues: list[Issue] = []

    for token in parser.parse("\n".join(body) + "\n"):
        if token.type != "fence" or token.map is None:
            continue
        start, end = token.map
        for line_no in range(start + offset + 1, end + offset + 1):
            fenced_lines.add(line_no)
        if end - start != token.content.count("\n") + 2:
            issues.append(
                Issue(
                    path=display_path,
                    line=start + offset + 1,
                    column=1,
                    severity="error",
                    rule="unclosed-code-fence",
                    message="围栏代码块未闭合",
                    snippet=lines[start + offset].strip(),
                )
            )
    return fenced_lines, issues


def _collect_display_math_lines(lines: list[str], fenced_lines: set[int], offset: int) -> set[int]:
    """Return 1-based line numbers inside $$ ... $$ display math blocks."""
    math_lines: set[int] = set()
    in_block = False
    for idx in range(offset, len(lines)):
        line_no = idx + 1
        if line_no in fenced_lines:
            continue
        stripped = lines[idx].strip()
        if stripped.startswith("$$"):
            math_lines.add(line_no)
            if stripped == "$$" or not stripped.endswith("$$") or len(stripped) == 2:
                in_block = not in_block
        elif in_block:
            math_lines.add(line_no)
    return math_lines


def _split_table_row(line: str) -> list[str]:
    """Split a Markdown table row into cells, ignoring escaped pipes and pipes inside inline code."""
    s = line.strip().removeprefix("|")
    if s.endswith("|") and not s.endswith(r"\|"):
        s = s[:-1]

    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    code_ticks = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and not in_code and i + 1 < len(s):
            buf.append(ch)
            buf.append(s[i + 1])
            i += 2
            continue
        if ch == "`":
            j = i
            while j < len(s) and s[j] == "`":
                j += 1
            ticks = j - i
            if not in_code:
                in_code = True
                code_ticks = ticks
            elif ticks == code_ticks:
                in_code = False
                code_ticks = 0
            buf.append(s[i:j])
            i = j
            continue
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _is_table_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = _split_table_row(line)
    if not cells:
        return False
    return all(bool(TABLE_SEP_CELL_RE.match(cell)) for cell in cells)


def _check_tables(
    lines: list[str], skip_lines: set[int], offset: int, display_path: str
) -> list[Issue]:
    issues: list[Issue] = []
    i = offset
    while i + 1 < len(lines):
        line_no = i + 1
        if line_no in skip_lines or (line_no + 1) in skip_lines:
            i += 1
            continue
        if "|" in lines[i] and _is_table_separator(lines[i + 1]):
            header_cols = len(_split_table_row(lines[i]))
            sep_cols = len(_split_table_row(lines[i + 1]))
            if sep_cols != header_cols:
                issues.append(
                    Issue(
                        path=display_path,
                        line=i + 2,
                        column=1,
                        severity="error",
                        rule="table-column-mismatch",
                        message=f"表格分隔行列数 ({sep_cols}) 与表头列数 ({header_cols}) 不一致",
                        snippet=lines[i + 1].strip(),
                    )
                )
            j = i + 2
            while (
                j < len(lines)
                and (j + 1) not in skip_lines
                and "|" in lines[j]
                and lines[j].strip()
            ):
                row_cols = len(_split_table_row(lines[j]))
                if row_cols != header_cols:
                    issues.append(
                        Issue(
                            path=display_path,
                            line=j + 1,
                            column=1,
                            severity="error",
                            rule="table-column-mismatch",
                            message=f"表格数据行列数 ({row_cols}) 与表头列数 ({header_cols}) 不一致",
                            snippet=lines[j].strip(),
                        )
                    )
                j += 1
            i = j
            continue
        i += 1
    return issues


def _mask_inline_code(line: str) -> tuple[str, list[tuple[int, int]]]:
    """Replace inline code spans with spaces of equal length so column indices stay exact."""
    chars = list(line)
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(line)
    while i < n:
        if line[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if line[i] == "`":
            j = i
            while j < n and line[j] == "`":
                j += 1
            ticks = j - i
            close_pos = line.find("`" * ticks, j)
            while close_pos != -1:
                after = close_pos + ticks
                if (close_pos == 0 or line[close_pos - 1] != "`") and (
                    after == n or line[after] != "`"
                ):
                    break
                close_pos = line.find("`" * ticks, after)
            if close_pos != -1:
                end = close_pos + ticks
                spans.append((i, end))
                for k in range(i, end):
                    chars[k] = "\u0000"
                i = end
                continue
            i = j
            continue
        i += 1
    return "".join(chars), spans


def _is_likely_currency(line: str, start: int, end: int) -> bool:
    """Heuristic to avoid flagging two currency amounts like '$100 and $200' as inline math."""
    body = line[start + 1 : end - 1]
    if not body:
        return True
    # Currency usually starts with digits followed by space and prose words.
    return bool(re.match(r"^\d+(?:[.,]\d+)?\s+\S", body))


def _extract_link_target(inner: str) -> tuple[str, str]:
    """Split link destination from optional title; return (destination, rest)."""
    stripped = inner.strip()
    if stripped.startswith("<"):
        close = stripped.find(">")
        if close != -1:
            return stripped[: close + 1], stripped[close + 1 :]
    # Otherwise destination is up to first unescaped whitespace (unless path contains spaces with no title).
    quote_match = re.search(r"""\s+(["'][^"']*["']|\([^)]*\))\s*$""", stripped)
    if quote_match:
        return stripped[: quote_match.start()].strip(), stripped[quote_match.start() :]
    return stripped, ""


def _check_line(line: str, line_no: int, display_path: str) -> list[Issue]:
    issues: list[Issue] = []

    # Step 1: mask inline code spans (preserving string length & column offsets).
    no_code, _ = _mask_inline_code(line)

    # Step 2: check reference definition link target angle brackets.
    ref_match = REF_DEF_RE.match(no_code)
    if ref_match:
        target = ref_match.group("target")
        if not (target.startswith("<") and target.endswith(">")):
            col = ref_match.start("target") + 1
            issues.append(
                Issue(
                    path=display_path,
                    line=line_no,
                    column=col,
                    severity="error",
                    rule="link-angle-brackets",
                    message="引用链接目标未使用尖括号包围",
                    snippet=f"{target} → <{target}>",
                )
            )
        return issues

    # Step 3: check inline links/images angle brackets, and mask link targets so URLs aren't prose-checked.
    masked_chars = list(no_code)
    for m in INLINE_LINK_RE.finditer(no_code):
        dest, _rest = _extract_link_target(m.group("inner"))
        if dest and not (dest.startswith("<") and dest.endswith(">")):
            col = m.start("inner") + 1
            issues.append(
                Issue(
                    path=display_path,
                    line=line_no,
                    column=col,
                    severity="error",
                    rule="link-angle-brackets",
                    message="Markdown 链接目标未使用尖括号包围",
                    snippet=f"({dest}) → (<{dest}>)",
                )
            )
        # Mask the `(inner)` part so URLs / file paths inside links are not checked as prose.
        for k in range(m.start("inner") - 1, m.end()):
            masked_chars[k] = "\u0000"

    after_links = "".join(masked_chars)

    # Step 4: check inline math delimiter spaces, then mask inline math spans.
    math_chars = list(after_links)
    for m in INLINE_MATH_RE.finditer(after_links):
        if _is_likely_currency(after_links, m.start(), m.end()):
            continue
        body = m.group("body")
        if body.startswith((" ", "\t")) or body.endswith((" ", "\t")):
            issues.append(
                Issue(
                    path=display_path,
                    line=line_no,
                    column=m.start() + 1,
                    severity="error",
                    rule="math-delimiter-space",
                    message="行内公式定界符 `$` 与公式内容之间不得有空格",
                    snippet=f"{m.group(0)} → ${body.strip()}$",
                )
            )
        for k in range(m.start(), m.end()):
            math_chars[k] = "\u0000"

    prose = "".join(math_chars)

    # Step 5: mask bare URLs and HTML tags.
    prose_chars = list(prose)
    for pattern in (URL_RE, HTML_TAG_RE):
        for m in pattern.finditer(prose):
            for k in range(m.start(), m.end()):
                prose_chars[k] = "\u0000"
    prose = "".join(prose_chars)

    # Step 6: forbidden punctuation & full-width space.
    for m in FORBIDDEN_PUNCT_RE.finditer(prose):
        ch = m.group(0)
        replacement = FORBIDDEN_PUNCT_MAP[ch]
        label = "全角空格" if ch == "\u3000" else f"「{ch}」"
        target_label = "半角空格" if replacement == " " else f"「{replacement}」"
        issues.append(
            Issue(
                path=display_path,
                line=line_no,
                column=m.start() + 1,
                severity="error",
                rule="forbidden-punctuation",
                message=f"不应使用{label}，建议改用{target_label}",
                snippet=_excerpt(line, m.start(), m.end()),
            )
        )

    # Step 7: CJK <-> Latin/digit spacing.
    for m in SPACE_CJK_LATIN_RE.finditer(prose):
        left = m.group("left") or m.group("left2")
        right = m.group("right") or m.group("right2")
        issues.append(
            Issue(
                path=display_path,
                line=line_no,
                column=m.start() + 1,
                severity="error",
                rule="space-cjk-latin",
                message="中英文或中文与数字之间缺少空格",
                snippet=f"「{left}{right}」→「{left} {right}」",
            )
        )

    # Step 8: degree / percent should not have space after digit.
    for m in DEGREE_PERCENT_SPACE_RE.finditer(prose):
        issues.append(
            Issue(
                path=display_path,
                line=line_no,
                column=m.start() + 1,
                severity="error",
                rule="no-space-degree-percent",
                message="数字与度数或百分比符号之间不应加空格",
                snippet=f"「{m.group(0)}」→「{m.group(1)}{m.group(2)}」",
            )
        )

    # Step 9: no spaces adjacent to full-width punctuation.
    # Strip Markdown structural prefixes (list markers, headings, blockquotes) before checking.
    prefix_match = re.match(r"^[ \t]*(?:[>#*+-]+[ \t]+|\d+\.[ \t]+)+", prose)
    prefix_len = prefix_match.end() if prefix_match else 0
    body_for_fw = ("\u0000" * prefix_len) + prose[prefix_len:]

    for m in FULLWIDTH_SPACE_BEFORE_RE.finditer(body_for_fw):
        # Skip if preceding non-space char is a placeholder for code/math/link or table pipe.
        prev_idx = m.start() - 1
        if prev_idx >= 0 and body_for_fw[prev_idx] in ("\u0000", "|"):
            continue
        issues.append(
            Issue(
                path=display_path,
                line=line_no,
                column=m.start() + 1,
                severity="error",
                rule="no-space-fullwidth",
                message="全角标点前不应有空格",
                snippet=_excerpt(line, m.start(), m.end()),
            )
        )
    for m in FULLWIDTH_SPACE_AFTER_RE.finditer(body_for_fw):
        next_idx = m.end()
        if next_idx < len(body_for_fw) and body_for_fw[next_idx] in ("\u0000", "|"):
            continue
        issues.append(
            Issue(
                path=display_path,
                line=line_no,
                column=m.start() + 1,
                severity="error",
                rule="no-space-fullwidth",
                message="全角标点后不应有空格",
                snippet=_excerpt(line, m.start(), m.end()),
            )
        )

    # Step 10: half-width punctuation directly adjacent to CJK characters.
    for m in HALFWIDTH_PUNCT_IN_CJK_RE.finditer(prose):
        punct = m.group(1) or m.group(2)
        issues.append(
            Issue(
                path=display_path,
                line=line_no,
                column=m.start() + 1,
                severity="error",
                rule="halfwidth-punct-in-cjk",
                message=f"中文语境中误用半角标点 `{punct}`",
                snippet=_excerpt(line, m.start(), m.end()),
            )
        )

    # Step 11: proper noun casing & unidiomatic abbreviations.
    for m in PROPER_NOUN_TOKEN_RE.finditer(prose):
        token = m.group(1)
        lower = token.lower()
        if lower in PROPER_NOUNS and token != PROPER_NOUNS[lower]:
            issues.append(
                Issue(
                    path=display_path,
                    line=line_no,
                    column=m.start() + 1,
                    severity="error",
                    rule="proper-noun-case",
                    message=f"专有名词大小写不规范: `{token}` → `{PROPER_NOUNS[lower]}`",
                    snippet=token,
                )
            )
        elif token in UNIDIOMATIC_ABBR:
            expected, sev = UNIDIOMATIC_ABBR[token]
            issues.append(
                Issue(
                    path=display_path,
                    line=line_no,
                    column=m.start() + 1,
                    severity=sev,
                    rule="unidiomatic-abbr",
                    message=f"疑似不地道缩写 `{token}`，如指代 {expected} 请改为 `{expected}`",
                    snippet=token,
                )
            )

    return issues


def _excerpt(line: str, start: int, end: int, radius: int = 8) -> str:
    lo = max(0, start - radius)
    hi = min(len(line), end + radius)
    return line[lo:hi].strip()


def check_text(text: str, display_path: str = "<stdin>") -> list[Issue]:
    lines = text.splitlines()
    offset = _frontmatter_end(lines)
    fenced_lines, issues = _collect_fenced_lines(lines, offset, display_path)
    math_lines = _collect_display_math_lines(lines, fenced_lines, offset)
    skip_lines = fenced_lines | math_lines

    issues.extend(_check_tables(lines, skip_lines, offset, display_path))

    for idx in range(offset, len(lines)):
        line_no = idx + 1
        if line_no in skip_lines:
            continue
        issues.extend(_check_line(lines[idx], line_no, display_path))

    issues.sort(key=lambda item: (item.line, item.column, item.rule))
    return issues


def check_file(path: Path) -> list[Issue]:
    text = path.read_text(encoding="utf-8")
    return check_text(text, display_path=path.as_posix())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_format",
        description="对 Markdown 文章执行确定性格式检查（只报告问题，不修改文件）。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="要检查的 Markdown 文件路径（传 `-` 从 stdin 读取）。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="以 JSON 格式输出检查结果。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    all_issues: list[Issue] = []
    file_count = 0

    for raw_path in args.paths:
        if raw_path == "-":
            content = sys.stdin.read()
            all_issues.extend(check_text(content, display_path="<stdin>"))
            file_count += 1
            continue

        file_path = Path(raw_path)
        if not file_path.is_file():
            print(f"Error: file not found: {raw_path}", file=sys.stderr)
            return 2
        try:
            all_issues.extend(check_file(file_path))
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: failed to read {raw_path}: {exc}", file=sys.stderr)
            return 2
        file_count += 1

    errors = sum(1 for item in all_issues if item.severity == "error")
    warnings = sum(1 for item in all_issues if item.severity == "warning")

    if args.as_json:
        payload = {
            "files_checked": file_count,
            "errors": errors,
            "warnings": warnings,
            "issues": [asdict(item) for item in all_issues],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in all_issues:
            print(
                f"{item.path}:{item.line}:{item.column} [{item.severity}] "
                f"{item.rule}: {item.message} ({item.snippet})"
            )
        print(f"Checked {file_count} file(s): {errors} error(s), {warnings} warning(s)")

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
