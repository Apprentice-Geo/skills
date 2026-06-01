import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_skill_frontmatter() -> dict:
    skill_text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", skill_text, flags=re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter."
    payload = yaml.safe_load(match.group(1))
    assert isinstance(payload, dict)
    return payload


def test_skill_frontmatter_matches_required_skill_metadata() -> None:
    payload = load_skill_frontmatter()

    assert payload["name"] == REPO_ROOT.name
    assert payload.get("description")
    assert "Bilibili" in payload["description"] or "B站" in payload["description"]
    assert payload.get("compatibility")
    assert payload.get("metadata", {}).get("Github", "").startswith("https://")


def test_skill_referenced_files_exist() -> None:
    expected_paths = [
        "SKILL.md",
        "README.md",
        "requirements.txt",
        "scripts/run_pipeline.py",
        "scripts/validate_summary.py",
        "scripts/fetch_audio.py",
        "scripts/transcribe.py",
        "references/error-handling.md",
        "assets/summary_instructions.md",
        "assets/summary_template_en.md",
        "assets/summary_template_zh.md",
    ]

    for relative_path in expected_paths:
        assert (REPO_ROOT / relative_path).exists(), f"Missing {relative_path}"


def test_readme_declares_agent_skill_standard_and_main_usage() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Agent Skills" in readme
    assert "scripts\\run_pipeline.py" in readme or "scripts/run_pipeline.py" in readme
    assert "--skip-subtitles" in readme


def test_skill_declares_summary_validator_usage() -> None:
    skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "scripts\\validate_summary.py" in skill or "scripts/validate_summary.py" in skill
