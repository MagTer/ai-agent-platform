"""Skill registry with startup validation.

This module provides a centralized registry for skills that validates
all skill files at startup, ensuring tool references are valid and
providing fast skill lookup during execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from core.tools import ToolRegistry

LOGGER = logging.getLogger(__name__)

# Default to a path relative to this file if not in docker
DEFAULT_SKILLS_PATH = Path(__file__).parent.parent.parent.parent.parent / "skills"
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", str(DEFAULT_SKILLS_PATH)))


@dataclass
class Skill:
    """Validated skill definition loaded from markdown file."""

    name: str
    path: Path
    description: str = ""
    tools: list[str] = field(default_factory=list)
    model: str = "agentchat"
    max_turns: int = 10
    variables: list[str] = field(default_factory=list)
    raw_content: str = ""
    body_template: str = ""

    def render(self, args: dict[str, str] | None = None) -> str:
        """Render the skill body with provided arguments.

        Args:
            args: Template variables to substitute.

        Returns:
            Rendered skill prompt.

        Raises:
            ValueError: If required variables are missing.
        """
        args = args or {}

        # Check required variables
        if self.variables:
            missing = [v for v in self.variables if v not in args]
            if missing:
                raise ValueError(f"Missing required arguments for skill '{self.name}': {missing}")

        # Render template
        template = Template(self.body_template)
        return template.safe_substitute(args).strip()


class SkillRegistry:
    """Registry of validated skills with startup validation.

    Loads all skill markdown files at initialization and validates:
    - YAML frontmatter is parseable
    - Required 'name' field exists
    - Referenced tools exist in the ToolRegistry (warnings if not)

    Usage:
        registry = SkillRegistry(tool_registry)
        skill = registry.get("researcher")
        if skill:
            prompt = skill.render({"goal": "Find Python 3.12 features"})
    """

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        skills_dir: Path | None = None,
    ) -> None:
        """Initialize the skill registry and validate all skills.

        For synchronous initialization, loads skills sequentially.
        For async initialization with parallel loading, use create_async() instead.

        Args:
            tool_registry: Optional ToolRegistry for validating tool references.
            skills_dir: Optional override for skills directory path.
        """
        self._tool_registry = tool_registry
        self._skills_dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, Skill] = {}
        self._by_path: dict[str, Skill] = {}  # Path-based lookup for compatibility

        self._load_and_validate()

    @classmethod
    async def create_async(
        cls,
        tool_registry: ToolRegistry | None = None,
        skills_dir: Path | None = None,
    ) -> SkillRegistry:
        """Create SkillRegistry with async parallel loading (recommended).

        Loads all skill files in parallel for faster initialization.
        Preferred over synchronous __init__ for production use.

        Args:
            tool_registry: Optional ToolRegistry for validating tool references.
            skills_dir: Optional override for skills directory path.

        Returns:
            Initialized SkillRegistry instance.

        Example:
            registry = await SkillRegistry.create_async(tool_registry)
        """
        instance = cls.__new__(cls)
        instance._tool_registry = tool_registry
        instance._skills_dir = skills_dir or SKILLS_DIR
        instance._skills = {}
        instance._by_path = {}

        await instance._load_and_validate_async()

        return instance

    async def _load_skill_file_async(self, path: Path) -> tuple[Path, str] | None:
        """Load a single skill file asynchronously.

        Args:
            path: Path to the skill markdown file.

        Returns:
            Tuple of (path, content) if successful, None if failed.
        """
        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return path, content
        except Exception as e:
            LOGGER.warning("Failed to read skill file %s: %s", path, e)
            return None

    async def _load_and_validate_async(self) -> None:
        """Load all skills from the skills directory asynchronously and validate them."""
        if not self._skills_dir.exists():
            LOGGER.warning("Skills directory does not exist: %s", self._skills_dir)
            return

        # Find all skill files
        skill_paths = list(self._skills_dir.rglob("*.md"))

        # Load all files in parallel
        tasks = [self._load_skill_file_async(p) for p in skill_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_count = 0
        invalid_count = 0
        tool_warnings: list[str] = []

        # Parse loaded content
        for result in results:
            if isinstance(result, BaseException):
                LOGGER.warning("Failed to load skill: %s", result)
                invalid_count += 1
                continue

            if result is None:
                invalid_count += 1
                continue

            path, content = result

            try:
                skill = self._parse_skill_content(path, content)
                if skill:
                    self._register_skill(skill, tool_warnings)
                    valid_count += 1
                else:
                    invalid_count += 1

            except Exception as e:
                LOGGER.warning("Failed to parse skill %s: %s", path, e)
                invalid_count += 1

        self._log_load_summary(valid_count, invalid_count, tool_warnings)

    def _load_and_validate(self) -> None:
        """Load all skills from the skills directory and validate them."""
        if not self._skills_dir.exists():
            LOGGER.warning("Skills directory does not exist: %s", self._skills_dir)
            return

        valid_count = 0
        invalid_count = 0
        tool_warnings: list[str] = []

        for path in self._skills_dir.rglob("*.md"):
            try:
                skill = self._load_skill(path)
                if skill:
                    self._register_skill(skill, tool_warnings)
                    valid_count += 1

            except Exception as e:
                LOGGER.warning("Failed to load skill %s: %s", path, e)
                invalid_count += 1

        self._log_load_summary(valid_count, invalid_count, tool_warnings)

    def _log_load_summary(
        self, valid_count: int, invalid_count: int, tool_warnings: list[str]
    ) -> None:
        """Log skill loading summary with tool warnings.

        Args:
            valid_count: Number of successfully loaded skills.
            invalid_count: Number of failed skill loads.
            tool_warnings: List of tool reference warnings.
        """
        LOGGER.info(
            "SkillRegistry loaded: %d valid skills, %d invalid",
            valid_count,
            invalid_count,
        )

        if tool_warnings:
            max_warnings = 10
            for warning in tool_warnings[:max_warnings]:
                LOGGER.warning(warning)
            if len(tool_warnings) > max_warnings:
                LOGGER.warning("... and %d more tool warnings", len(tool_warnings) - max_warnings)

    def _parse_skill_content(self, path: Path, content: str) -> Skill | None:
        """Parse skill content from markdown.

        Args:
            path: Path to the skill file (for metadata).
            content: File content to parse.

        Returns:
            Skill object if valid, None if invalid.
        """
        # Parse frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_raw = parts[1]
                body_template = parts[2]
                metadata = yaml.safe_load(frontmatter_raw) or {}
            else:
                # Malformed frontmatter
                LOGGER.warning("Malformed frontmatter in %s", path)
                return None
        else:
            # No frontmatter
            metadata = {}
            body_template = content

        # Extract skill name (required)
        skill_name = metadata.get("name")
        if not skill_name:
            # Use path-based name as fallback
            relative_path = path.relative_to(self._skills_dir).with_suffix("")
            skill_name = str(relative_path).replace("\\", "/")

        # Extract other fields
        return Skill(
            name=skill_name,
            path=path,
            description=metadata.get("description", ""),
            tools=metadata.get("tools", []),
            model=metadata.get("model", "agentchat"),
            max_turns=metadata.get("max_turns", 10),
            variables=metadata.get("variables", []),
            raw_content=content,
            body_template=body_template,
        )

    def _register_skill(self, skill: Skill, tool_warnings: list[str]) -> None:
        """Register a skill in all lookup indexes and validate tool references.

        Args:
            skill: Validated Skill object to register.
            tool_warnings: List to append tool validation warnings to.
        """
        # Register by name
        self._skills[skill.name] = skill

        # Also register by path-based name for compatibility
        relative_path = skill.path.relative_to(self._skills_dir).with_suffix("")
        path_name = str(relative_path).replace("\\", "/")
        self._by_path[path_name] = skill
        self._by_path[skill.path.stem] = skill  # Also by filename only

        # Validate tool references
        if self._tool_registry and skill.tools:
            for tool_name in skill.tools:
                if not self._tool_registry.get(tool_name):
                    tool_warnings.append(
                        f"Skill '{skill.name}' references missing tool '{tool_name}'"
                    )

    def _load_skill(self, path: Path) -> Skill | None:
        """Load a single skill from a markdown file.

        Args:
            path: Path to the skill markdown file.

        Returns:
            Skill object if valid, None if invalid.
        """
        content = path.read_text(encoding="utf-8")
        return self._parse_skill_content(path, content)

    def get(self, name: str) -> Skill | None:
        """Look up skill by name with fallback matching.

        Tries multiple lookup strategies in order:
        1. Exact skill name (from frontmatter)
        2. Path-based name (e.g., "general/researcher")
        3. Filename only (e.g., "researcher")

        Args:
            name: Skill name to look up.

        Returns:
            Skill if found, None otherwise.
        """
        # Try exact name first
        if name in self._skills:
            return self._skills[name]

        # Try path-based lookup
        if name in self._by_path:
            return self._by_path[name]

        return None

    def available(self) -> list[str]:
        """List all registered skill names.

        Returns:
            List of skill names from frontmatter.
        """
        return list(self._skills.keys())

    def get_index(self) -> str:
        """Get formatted skill index for LLM prompts.

        Returns:
            Bulleted markdown list of skills with descriptions.
        """
        if not self._skills:
            return "(No skills loaded)"

        lines = []
        for skill in sorted(self._skills.values(), key=lambda s: s.name):
            desc = skill.description or "No description"
            lines.append(f"* [{skill.name}]: {desc}")

        return "\n".join(lines)

    def get_skill_names(self) -> set[str]:
        """Get all valid skill lookup names.

        Includes frontmatter names, path-based names, and filenames.

        Returns:
            Set of all valid skill name aliases.
        """
        names: set[str] = set()
        names.update(self._skills.keys())
        names.update(self._by_path.keys())
        return names

    def validate_skill(self, name: str) -> tuple[bool, str]:
        """Validate a skill name and return validation status.

        Args:
            name: Skill name to validate.

        Returns:
            Tuple of (is_valid, message).
        """
        skill = self.get(name)
        if not skill:
            available = ", ".join(sorted(self._skills.keys())[:5])
            return False, f"Unknown skill '{name}'. Available: {available}..."

        # Check tool references
        if self._tool_registry and skill.tools:
            missing_tools = [t for t in skill.tools if not self._tool_registry.get(t)]
            if missing_tools:
                return True, f"Warning: Skill '{name}' references missing tools: {missing_tools}"

        return True, f"Skill '{name}' is valid"


__all__ = ["Skill", "SkillRegistry"]
