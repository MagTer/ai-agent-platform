"""Tests for enhanced skills overlay admin endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from core.skills.registry import Skill, SkillRegistry

_SKILL_FRONTMATTER = (
    "---\n"
    "name: {name}\n"
    "description: {desc}\n"
    "tools: [{tools}]\n"
    "model: agentchat\n"
    "max_turns: {turns}\n"
    "---\n\n"
    "Body\n"
)


def _write_skill(
    directory: Path,
    file_name: str,
    name: str,
    desc: str = "A skill",
    tools: str = "",
    turns: int = 5,
) -> None:
    """Write a minimal skill markdown file to a directory."""
    content = _SKILL_FRONTMATTER.format(name=name, desc=desc, tools=tools, turns=turns)
    (directory / file_name).write_text(content, encoding="utf-8")


def _make_skill(
    name: str = "researcher",
    file_name: str = "researcher.md",
    skills_dir: Path | None = None,
) -> Skill:
    """Create a mock global Skill instance."""
    path = (skills_dir or Path("/app/skills/general")) / file_name
    return Skill(
        name=name,
        path=path,
        description="Web research skill",
        tools=["web_search", "web_fetch"],
        model="agentchat",
        max_turns=10,
        raw_content="---\nname: " + name + "\n---\n\n# Researcher\n\nDo research.",
        body_template="\n\n# Researcher\n\nDo research.",
    )


def test_list_all_skills_method() -> None:
    """Test SkillRegistry.list_all_skills returns correct structure."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        general_dir = tmp_path / "general"
        general_dir.mkdir()
        _write_skill(general_dir, "test.md", name="test", desc="Test")

        reg = SkillRegistry(tool_registry=None, skills_dir=tmp_path)
        skills = reg.list_all_skills()

        assert len(skills) == 1
        assert skills[0]["name"] == "test"
        assert skills[0]["category"] == "general"
        assert skills[0]["file_name"] == "test.md"


def test_list_all_skills_multiple_categories() -> None:
    """Test list_all_skills groups correctly by subdirectory category."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        general_dir = tmp_path / "general"
        work_dir = tmp_path / "work"
        general_dir.mkdir()
        work_dir.mkdir()

        _write_skill(
            general_dir, "researcher.md", name="researcher", desc="Research", tools="web_search"
        )
        _write_skill(
            work_dir,
            "backlog.md",
            name="backlog_manager",
            desc="Backlog",
            tools="azure_devops",
            turns=8,
        )

        reg = SkillRegistry(tool_registry=None, skills_dir=tmp_path)
        skills = reg.list_all_skills()

        assert len(skills) == 2
        names = {s["name"] for s in skills}
        assert "researcher" in names
        assert "backlog_manager" in names

        researcher = next(s for s in skills if s["name"] == "researcher")
        assert researcher["category"] == "general"

        backlog = next(s for s in skills if s["name"] == "backlog_manager")
        assert backlog["category"] == "work"


def test_list_all_skills_top_level_category() -> None:
    """Test that skills at root of skills_dir get 'unknown' category."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_skill(tmp_path, "rootskill.md", name="rootskill", desc="Root")

        reg = SkillRegistry(tool_registry=None, skills_dir=tmp_path)
        skills = reg.list_all_skills()

        assert len(skills) == 1
        assert skills[0]["category"] == "unknown"


def test_list_all_skills_sorted_by_name() -> None:
    """Test that list_all_skills returns skills sorted alphabetically."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        general_dir = tmp_path / "general"
        general_dir.mkdir()

        for skill_name in ["zebra", "alpha", "midway"]:
            _write_skill(
                general_dir, f"{skill_name}.md", name=skill_name, desc=f"Skill {skill_name}"
            )

        reg = SkillRegistry(tool_registry=None, skills_dir=tmp_path)
        skills = reg.list_all_skills()

        names = [str(s["name"]) for s in skills]
        assert names == sorted(names)


def test_list_all_skills_metadata_fields() -> None:
    """Test that all expected metadata fields are present in each skill dict."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        general_dir = tmp_path / "general"
        general_dir.mkdir()
        _write_skill(
            general_dir,
            "myskill.md",
            name="myskill",
            desc="My skill description",
            tools="web_search, web_fetch",
            turns=7,
        )

        reg = SkillRegistry(tool_registry=None, skills_dir=tmp_path)
        skills = reg.list_all_skills()

        assert len(skills) == 1
        skill = skills[0]
        expected_keys = {
            "name",
            "file_name",
            "description",
            "model",
            "tools",
            "max_turns",
            "category",
        }  # noqa: E501
        assert expected_keys <= set(skill.keys())
        assert skill["name"] == "myskill"
        assert skill["file_name"] == "myskill.md"
        assert skill["description"] == "My skill description"
        assert skill["category"] == "general"


def test_mock_registry_list_all_skills() -> None:
    """Test SkillRegistry mock returns list_all_skills correctly."""
    registry = MagicMock(spec=SkillRegistry)
    registry.list_all_skills.return_value = [
        {
            "name": "researcher",
            "file_name": "researcher.md",
            "description": "Web research skill",
            "model": "agentchat",
            "tools": ["web_search", "web_fetch"],
            "max_turns": 10,
            "category": "general",
        }
    ]
    registry.get.return_value = _make_skill()

    result = registry.list_all_skills()
    assert len(result) == 1
    assert result[0]["name"] == "researcher"
    assert result[0]["category"] == "general"

    skill = registry.get("researcher")
    assert skill is not None
    assert skill.name == "researcher"
    assert skill.raw_content.startswith("---")
