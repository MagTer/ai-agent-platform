"""Skills-native execution module.

This module provides the infrastructure for running skills as the primary
execution unit, with scoped tool access and startup validation.
"""

from typing import Protocol

from core.skills.executor import SkillExecutor
from core.skills.registry import Skill, SkillRegistry


class SkillRegistryProtocol(Protocol):
    """Protocol for skill registry implementations.

    This allows both SkillRegistry and CompositeSkillRegistry to be used
    interchangeably in type hints.
    """

    def get(self, name: str) -> Skill | None:
        """Get skill by name."""
        ...

    def get_index(self) -> str:
        """Get formatted skill index for LLM prompts."""
        ...

    def available(self) -> list[str]:
        """List all registered skill names."""
        ...

    def get_skill_names(self) -> set[str]:
        """Get all valid skill lookup names."""
        ...


__all__ = ["Skill", "SkillExecutor", "SkillRegistry", "SkillRegistryProtocol"]
