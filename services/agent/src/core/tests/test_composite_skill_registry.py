"""Tests for CompositeSkillRegistry (per-context skill overlays)."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from core.skills.composite import CompositeSkillRegistry
from core.skills.registry import Skill, SkillRegistry


@pytest.fixture
def global_registry() -> Generator[SkillRegistry, None, None]:
    """Create a global skill registry with test skills."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "global"
        skills_dir.mkdir()

        # Create global skill
        (skills_dir / "global_skill.md").write_text(
            """---
name: "global_skill"
description: "A global skill"
tools: ["tool1"]
model: agentchat
max_turns: 5
---

Global skill body
"""
        )

        # Create another global skill
        (skills_dir / "shared_skill.md").write_text(
            """---
name: "shared_skill"
description: "A skill in both global and context"
tools: ["tool2"]
---

Global version of shared skill
"""
        )

        registry = SkillRegistry(skills_dir=skills_dir)
        yield registry


@pytest.fixture
def context_skills() -> Generator[dict[str, Skill], None, None]:
    """Create context-specific skills."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "context"
        skills_dir.mkdir()

        # Context-only skill
        (skills_dir / "context_skill.md").write_text(
            """---
name: "context_skill"
description: "A context-only skill"
tools: ["tool3"]
---

Context skill body
"""
        )

        # Override shared skill
        (skills_dir / "shared_skill.md").write_text(
            """---
name: "shared_skill"
description: "Context override of shared skill"
tools: ["tool4"]
---

Context version of shared skill (overrides global)
"""
        )

        from core.skills.registry import parse_skill_content

        skills = {}
        for path in skills_dir.glob("*.md"):
            content = path.read_text()
            skill = parse_skill_content(path, content, skills_dir)
            if skill:
                skills[skill.name] = skill

        yield skills


def test_composite_get_context_priority(
    global_registry: SkillRegistry, context_skills: dict[str, Skill]
) -> None:
    """Test that context skills take priority over global on name collision."""
    composite = CompositeSkillRegistry(global_registry, context_skills)

    # Context skill overrides global
    shared = composite.get("shared_skill")
    assert shared is not None
    assert shared.description == "Context override of shared skill"
    assert shared.tools == ["tool4"]


def test_composite_get_fallback_to_global(
    global_registry: SkillRegistry, context_skills: dict[str, Skill]
) -> None:
    """Test that composite falls back to global when no context skill matches."""
    composite = CompositeSkillRegistry(global_registry, context_skills)

    # Global-only skill
    global_skill = composite.get("global_skill")
    assert global_skill is not None
    assert global_skill.description == "A global skill"
    assert global_skill.tools == ["tool1"]


def test_composite_get_context_only(
    global_registry: SkillRegistry, context_skills: dict[str, Skill]
) -> None:
    """Test that context-only skills are accessible."""
    composite = CompositeSkillRegistry(global_registry, context_skills)

    # Context-only skill
    context_skill = composite.get("context_skill")
    assert context_skill is not None
    assert context_skill.description == "A context-only skill"
    assert context_skill.tools == ["tool3"]


def test_composite_get_index_merges(
    global_registry: SkillRegistry, context_skills: dict[str, Skill]
) -> None:
    """Test that get_index() merges global and context skills."""
    composite = CompositeSkillRegistry(global_registry, context_skills)

    index = composite.get_index()

    # Should contain all three unique skill names
    assert "global_skill" in index
    assert "context_skill" in index
    assert "shared_skill" in index

    # Context version should win for shared_skill
    assert "Context override of shared skill" in index
    assert "A skill in both global and context" not in index


def test_composite_available_merges(
    global_registry: SkillRegistry, context_skills: dict[str, Skill]
) -> None:
    """Test that available() merges global and context skill names."""
    composite = CompositeSkillRegistry(global_registry, context_skills)

    names = composite.available()

    # Should have all three unique names
    assert set(names) == {"global_skill", "shared_skill", "context_skill"}


def test_composite_get_skill_names(
    global_registry: SkillRegistry, context_skills: dict[str, Skill]
) -> None:
    """Test that get_skill_names() returns merged set."""
    composite = CompositeSkillRegistry(global_registry, context_skills)

    names = composite.get_skill_names()

    # Should return a set with all three names
    assert names == {"global_skill", "shared_skill", "context_skill"}


def test_composite_empty_context_skills(global_registry: SkillRegistry) -> None:
    """Test composite with empty context skills."""
    composite = CompositeSkillRegistry(global_registry, {})

    # Should fall back to global
    global_skill = composite.get("global_skill")
    assert global_skill is not None

    # Should have same available list as global
    assert set(composite.available()) == set(global_registry.available())


@pytest.mark.asyncio
async def test_load_skills_from_dir_success() -> None:
    """Test load_skills_from_dir() parses frontmatter correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)

        # Create valid skill file
        (skills_dir / "test_skill.md").write_text(
            """---
name: "test_skill"
description: "Test skill"
tools: ["tool1", "tool2"]
model: skillsrunner
max_turns: 10
---

Skill body here
"""
        )

        from core.skills.registry import SkillRegistry

        skills = await SkillRegistry.load_skills_from_dir(skills_dir)

        assert len(skills) == 1
        assert "test_skill" in skills
        skill = skills["test_skill"]
        assert skill.description == "Test skill"
        assert skill.tools == ["tool1", "tool2"]
        assert skill.model == "skillsrunner"
        assert skill.max_turns == 10


@pytest.mark.asyncio
async def test_load_skills_from_dir_missing_dir() -> None:
    """Test load_skills_from_dir() returns empty dict for missing dir."""
    from core.skills.registry import SkillRegistry

    skills = await SkillRegistry.load_skills_from_dir(Path("/nonexistent/path"))

    assert skills == {}


@pytest.mark.asyncio
async def test_load_skills_from_dir_invalid_frontmatter() -> None:
    """Test load_skills_from_dir() logs warning for invalid frontmatter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)

        # Create invalid skill file (malformed YAML)
        (skills_dir / "bad_skill.md").write_text(
            """---
name: "bad_skill
invalid yaml: [unclosed
---

Body
"""
        )

        from core.skills.registry import SkillRegistry

        skills = await SkillRegistry.load_skills_from_dir(skills_dir)

        # Should return empty dict and log warning (not crash)
        assert skills == {}


@pytest.mark.asyncio
async def test_load_skills_from_dir_subdirectories() -> None:
    """Test load_skills_from_dir() finds skills in subdirectories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        subdir = skills_dir / "general"
        subdir.mkdir()

        # Create skill in subdirectory
        (subdir / "researcher.md").write_text(
            """---
name: "researcher"
description: "Research skill"
tools: ["web_search"]
---

Research body
"""
        )

        from core.skills.registry import SkillRegistry

        skills = await SkillRegistry.load_skills_from_dir(skills_dir)

        assert len(skills) == 1
        assert "researcher" in skills
