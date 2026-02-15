"""Composite skill registry for per-context skill overlays.

This module provides CompositeSkillRegistry, a lightweight wrapper that overlays
per-context skills on the shared global SkillRegistry without mutating either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.skills import SkillRegistryProtocol
    from core.skills.registry import Skill


class CompositeSkillRegistry:
    """Overlays per-context skills on the shared global SkillRegistry.

    Lookups check context skills first, falling back to global.
    Created per-request in ServiceFactory when context skills exist.

    This implements the same interface as SkillRegistry (duck-typed):
    - get(name) -> Skill | None
    - get_index() -> str
    - available() -> list[str]
    - get_skill_names() -> set[str]
    """

    def __init__(
        self,
        global_registry: SkillRegistryProtocol,
        context_skills: dict[str, Skill],
    ) -> None:
        """Initialize composite registry.

        Args:
            global_registry: The shared global skill registry (Protocol).
            context_skills: Per-context skill overrides (name -> Skill).
        """
        self._global = global_registry
        self._context_skills = context_skills

    def get(self, name: str) -> Skill | None:
        """Look up skill by name with context override priority.

        Args:
            name: Skill name to look up.

        Returns:
            Skill if found (context takes priority), None otherwise.
        """
        # Context skills take priority (override by name)
        if name in self._context_skills:
            return self._context_skills[name]
        return self._global.get(name)

    def get_index(self) -> str:
        """Get formatted skill index for LLM prompts.

        Merges global and context skills, with context skills taking priority
        on name collisions.

        Returns:
            Bulleted markdown list of skills with descriptions.
        """
        # Merge: global skills + context overrides
        all_skills: dict[str, Skill] = {}

        # Start with global skills
        for name in self._global.available():
            skill = self._global.get(name)
            if skill:
                all_skills[name] = skill

        # Overlay context skills (wins on collision)
        all_skills.update(self._context_skills)

        # Format as bulleted list
        if not all_skills:
            return "(No skills loaded)"

        lines = []
        for skill in sorted(all_skills.values(), key=lambda s: s.name):
            desc = skill.description or "No description"
            lines.append(f"* [{skill.name}]: {desc}")

        return "\n".join(lines)

    def available(self) -> list[str]:
        """List all registered skill names (global + context).

        Returns:
            Sorted list of skill names.
        """
        names = set(self._global.available())
        names.update(self._context_skills.keys())
        return sorted(names)

    def get_skill_names(self) -> set[str]:
        """Get all valid skill lookup names (compatibility method).

        Returns:
            Set of all valid skill name aliases.
        """
        # For CompositeSkillRegistry, we only use explicit names
        # (no path-based lookup like the base SkillRegistry)
        return set(self.available())


__all__ = ["CompositeSkillRegistry"]
