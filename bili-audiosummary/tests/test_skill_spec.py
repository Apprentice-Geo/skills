import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "SKILL.md"
DOC_PATHS = (
    SKILL_PATH,
    REPO_ROOT / "README.md",
    REPO_ROOT / "references" / "architecture.md",
    REPO_ROOT / "references" / "error-handling.md",
)
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def load_skill() -> tuple[dict, str]:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", skill_text, flags=re.DOTALL)
    assert match, "SKILL.md must contain YAML frontmatter followed by Markdown."

    payload = yaml.safe_load(match.group(1))
    assert isinstance(payload, dict)
    return payload, match.group(2)


def test_skill_has_required_frontmatter_and_markdown_body() -> None:
    payload, body = load_skill()

    assert "name" in payload
    assert "description" in payload
    assert body.strip()


def test_skill_name_follows_agent_skills_standard() -> None:
    payload, _body = load_skill()
    name = payload["name"]

    assert isinstance(name, str)
    assert 1 <= len(name) <= 64
    assert NAME_PATTERN.fullmatch(name)
    assert name == REPO_ROOT.name


def test_skill_description_follows_agent_skills_standard() -> None:
    payload, _body = load_skill()
    description = payload["description"]

    assert isinstance(description, str)
    assert 1 <= len(description.strip()) <= 1024


def test_skill_description_covers_bilingual_trigger_boundary() -> None:
    payload, _body = load_skill()
    description = payload["description"]

    for term in ("B站", "BV", "音频总结", "笔记", "要点", "时间戳", "画面分析"):
        assert term in description


def test_skill_body_keeps_agent_facing_sections() -> None:
    _payload, body = load_skill()

    headings = re.findall(r"^## (.+)$", body, flags=re.MULTILINE)

    assert headings == ["Usage Scenarios", "Main Steps", "Processing Time"]


def test_documentation_local_markdown_links_resolve() -> None:
    for document_path in DOC_PATHS:
        document = document_path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_PATTERN.findall(document):
            target = raw_target.split("#", 1)[0]
            if not target or "://" in target:
                continue

            resolved_target = document_path.parent / target
            assert resolved_target.exists(), (
                f"Broken local link in {document_path.relative_to(REPO_ROOT)}: {target}"
            )
