"""Tests for the SkillRegistry."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.skills.registry import Skill, SkillRegistry


def test_list_all_skills_returns_metadata(tmp_path: Path) -> None:
    """Test that list_all_skills returns serializable skill metadata."""
    # Create test skill directory structure
    general_dir = tmp_path / "general"
    general_dir.mkdir()
    skill_file = general_dir / "test_skill.md"
    skill_file.write_text(
        "---\n"
        "name: test_skill\n"
        "description: A test skill\n"
        "model: agentchat\n"
        "tools: [web_search]\n"
        "max_turns: 5\n"
        "---\n\n"
        "# Test Skill\n\nInstructions here.\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(tool_registry=None, skills_dir=tmp_path)
    result = registry.list_all_skills()

    assert len(result) == 1
    skill_info = result[0]
    assert skill_info["name"] == "test_skill"
    assert skill_info["description"] == "A test skill"
    assert skill_info["model"] == "agentchat"
    assert skill_info["tools"] == ["web_search"]
    assert skill_info["max_turns"] == 5
    assert skill_info["category"] == "general"
    assert skill_info["file_name"] == "test_skill.md"


def test_list_all_skills_empty_registry(tmp_path: Path) -> None:
    """Test that list_all_skills returns empty list for empty registry."""
    registry = SkillRegistry(tool_registry=None, skills_dir=tmp_path)
    result = registry.list_all_skills()
    assert result == []


class TestSkill:
    """Tests for the Skill dataclass."""

    def test_render_simple(self) -> None:
        """Test rendering a skill with no variables."""
        skill = Skill(
            name="test_skill",
            path=Path("/fake/path"),
            body_template="Hello, this is a test skill.",
        )
        result = skill.render()
        assert result == "Hello, this is a test skill."

    def test_render_with_variables(self) -> None:
        """Test rendering a skill with template variables."""
        skill = Skill(
            name="test_skill",
            path=Path("/fake/path"),
            body_template="Hello $name, your goal is: $goal",
            variables=["name", "goal"],
        )
        result = skill.render({"name": "Alice", "goal": "find Python docs"})
        assert result == "Hello Alice, your goal is: find Python docs"

    def test_render_missing_required_variable(self) -> None:
        """Test that rendering fails with missing required variables."""
        skill = Skill(
            name="test_skill",
            path=Path("/fake/path"),
            body_template="Hello $name",
            variables=["name"],
        )
        with pytest.raises(ValueError, match="Missing required arguments"):
            skill.render({})


class TestSkillRegistry:
    """Tests for the SkillRegistry."""

    def test_registry_empty_dir(self) -> None:
        """Test registry with non-existent directory."""
        registry = SkillRegistry(skills_dir=Path("/nonexistent/path"))
        assert registry.available() == []
        assert registry.get_index() == "(No skills loaded)"

    def test_registry_loads_skills(self) -> None:
        """Test that registry loads skills from directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)

            # Create a test skill file
            skill_file = skills_dir / "researcher.md"
            skill_file.write_text(
                """---
name: researcher
description: Web research skill
tools:
  - web_search
  - fetch_url
model: agentchat
max_turns: 5
---
You are a research assistant.
"""
            )

            registry = SkillRegistry(skills_dir=skills_dir)

            assert "researcher" in registry.available()
            skill = registry.get("researcher")
            assert skill is not None
            assert skill.name == "researcher"
            assert skill.description == "Web research skill"
            assert skill.tools == ["web_search", "fetch_url"]
            assert skill.model == "agentchat"
            assert skill.max_turns == 5

    def test_registry_path_based_lookup(self) -> None:
        """Test that skills can be looked up by path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            general_dir = skills_dir / "general"
            general_dir.mkdir()

            skill_file = general_dir / "homey.md"
            skill_file.write_text(
                """---
name: homey
description: Smart home control
tools:
  - homey
---
Control smart home devices.
"""
            )

            registry = SkillRegistry(skills_dir=skills_dir)

            # Should find by name
            assert registry.get("homey") is not None
            # Should find by path
            assert registry.get("general/homey") is not None

    def test_registry_validates_tools(self) -> None:
        """Test that registry logs warnings for missing tools."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            skill_file = skills_dir / "test.md"
            skill_file.write_text(
                """---
name: test
tools:
  - nonexistent_tool
---
Test skill.
"""
            )

            # Create mock tool registry
            mock_tool_registry = MagicMock()
            mock_tool_registry.get.return_value = None

            # Should load but log warning
            registry = SkillRegistry(
                tool_registry=mock_tool_registry,
                skills_dir=skills_dir,
            )

            assert "test" in registry.available()

    def test_get_skill_names(self) -> None:
        """Test get_skill_names returns all lookup names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            skill_file = skills_dir / "myskill.md"
            skill_file.write_text(
                """---
name: my_skill
---
Test.
"""
            )

            registry = SkillRegistry(skills_dir=skills_dir)
            names = registry.get_skill_names()

            # Should include both frontmatter name and filename
            assert "my_skill" in names
            assert "myskill" in names

    def test_validate_skill_valid(self) -> None:
        """Test validate_skill returns valid for existing skill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            skill_file = skills_dir / "test.md"
            skill_file.write_text(
                """---
name: test
---
Test.
"""
            )

            registry = SkillRegistry(skills_dir=skills_dir)
            is_valid, msg = registry.validate_skill("test")

            assert is_valid
            assert "valid" in msg.lower()

    def test_validate_skill_invalid(self) -> None:
        """Test validate_skill returns invalid for non-existent skill."""
        registry = SkillRegistry(skills_dir=Path("/nonexistent"))
        is_valid, msg = registry.validate_skill("nonexistent")

        assert not is_valid
        assert "Unknown skill" in msg

    def test_get_index_formatted(self) -> None:
        """Test get_index returns formatted skill list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            skill_file = skills_dir / "test.md"
            skill_file.write_text(
                """---
name: test
description: A test skill
---
Test.
"""
            )

            registry = SkillRegistry(skills_dir=skills_dir)
            index = registry.get_index()

            assert "* [test]: A test skill" in index
