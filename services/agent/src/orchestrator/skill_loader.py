import glob
import logging
import os
from dataclasses import dataclass, field

import yaml

LOGGER = logging.getLogger(__name__)


@dataclass
class SkillInput:
    name: str
    required: bool = False
    description: str = ""


@dataclass
class Skill:
    name: str
    description: str
    inputs: list[SkillInput]
    permission: str
    prompt_template: str
    file_path: str
    tools: list[str] = field(default_factory=list)


class SkillLoader:
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = skills_dir
        self.skills: dict[str, Skill] = {}

    def load_skills(self) -> dict[str, Skill]:
        """Recursively scans the skills directory for .md files."""
        LOGGER.info(f"Scanning for skills in {self.skills_dir}...")
        pattern = os.path.join(self.skills_dir, "**", "*.md")
        files = glob.glob(pattern, recursive=True)

        loaded_skills = {}

        for file_path in files:
            try:
                skill = self._parse_skill_file(file_path)
                if skill:
                    loaded_skills[skill.name] = skill
            except Exception as e:
                LOGGER.error(f"Failed to load skill from {file_path}: {e}")

        self.skills = loaded_skills
        LOGGER.info(f"Loaded {len(self.skills)} skills.")
        return self.skills

    def get_registry_index(self) -> str:
        """Return a bulleted list of available skills for the prompt."""
        if not self.skills:
            self.load_skills()

        if not self.skills:
            return "(No skills loaded)"

        lines = []
        for name, skill in self.skills.items():
            lines.append(f"• [{name}]: {skill.description}")
        return "\n".join(lines)

    def _parse_skill_file(self, file_path: str) -> Skill | None:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        # Manual Frontmatter Parsing
        if not content.startswith("---"):
            LOGGER.warning(f"Skipping {file_path}: Missing YAML frontmatter start.")
            return None

        parts = content.split("---", 2)
        if len(parts) < 3:
            LOGGER.warning(f"Skipping {file_path}: Invalid frontmatter format.")
            return None

        frontmatter_raw = parts[1]
        template_content = parts[2].strip()

        try:
            metadata = yaml.safe_load(frontmatter_raw)
        except yaml.YAMLError as e:
            LOGGER.error(f"YAML error in {file_path}: {e}")
            return None

        if not isinstance(metadata, dict) or "name" not in metadata:
            LOGGER.warning(f"Skipping {file_path}: Missing 'name' in frontmatter.")
            return None

        inputs_data = metadata.get("inputs", [])
        skill_inputs = []
        if isinstance(inputs_data, list):
            for inp in inputs_data:
                if isinstance(inp, dict):
                    skill_inputs.append(
                        SkillInput(
                            name=inp.get("name", "unknown"),
                            required=inp.get("required", False),
                            description=inp.get("description", ""),
                        )
                    )

        # Read tools list from metadata, defaulting to empty list
        tools_list = metadata.get("tools", [])
        if not isinstance(tools_list, list):
            tools_list = []

        return Skill(
            name=metadata["name"],
            description=metadata.get("description", ""),
            inputs=skill_inputs,
            permission=metadata.get("permission", "read"),
            prompt_template=template_content,
            file_path=file_path,
            tools=[str(t) for t in tools_list if isinstance(t, str)],
        )
