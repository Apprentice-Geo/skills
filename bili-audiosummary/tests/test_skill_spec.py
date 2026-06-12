import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "SKILL.md"
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
