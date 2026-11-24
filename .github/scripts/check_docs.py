# /// script
# requires-python = "==3.12.*"
# dependencies = ["markdown-it-py==4.2.0", "PyYAML==6.0.3", "rumdl==0.3.5"]
# ///

import re
import subprocess
from pathlib import Path

import yaml
from markdown_it import MarkdownIt


def check_skill(path: Path, lines: list[str]) -> int:
    if not lines or lines[0] != "---":
        raise ValueError("missing opening YAML frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("missing closing YAML frontmatter delimiter") from None

    metadata = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise TypeError("Skill frontmatter must be a YAML mapping")
    name = metadata.get("name")
    if (
        not isinstance(name, str)
        or len(name) > 64
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
        or name != path.parent.name
    ):
        raise ValueError("name must be a valid Skill name matching its directory")
    description = metadata.get("description")
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > 1024
    ):
        raise ValueError("description must be non-empty text of up to 1024 characters")
    if not any(line.strip() for line in lines[end + 1 :]):
        raise ValueError("missing Skill instructions after frontmatter")
    return end + 1


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "*.md"], cwd=root
    ).decode("utf-8")
    paths = [Path(path) for path in tracked.split("\0") if path]
    parser = MarkdownIt()
    errors = 0
    for path in paths:
        try:
            lines = (root / path).read_text(encoding="utf-8").splitlines()
            offset = check_skill(path, lines) if path.name == "SKILL.md" else 0
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            print(f"{path.as_posix()}:1: {exc}", flush=True)
            errors += 1
            continue
        body = lines[offset:]
        for token in parser.parse("\n".join(body) + "\n"):
            if token.type != "fence" or token.map is None:
                continue
            start, end = token.map
            # A closed fence spans both delimiter lines and all content lines.
            if end - start != token.content.count("\n") + 2:
                print(
                    f"{path.as_posix()}:{start + offset + 1}: unclosed code fence",
                    flush=True,
                )
                errors += 1

    lint = subprocess.run(
        [
            "rumdl",
            "check",
            "--no-cache",
            "--no-config",
            "--enable",
            "MD001,MD051,MD056,MD057,MD092",
            "--",
            *(path.as_posix() for path in paths),
        ],
        cwd=root,
        check=False,
    )
    return int(errors > 0 or lint.returncode != 0)


if __name__ == "__main__":
    raise SystemExit(main())
