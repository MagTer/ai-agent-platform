import os
from pathlib import Path
from string import Template
from typing import Any

import yaml

# Default to a path relative to this file if not in docker
# This file is in src/core, so skills is likely ../../../skills
DEFAULT_SKILLS_PATH = Path(__file__).parent.parent.parent.parent / "skills"
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", str(DEFAULT_SKILLS_PATH)))


def load_command(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """
    Load a skill/command from markdown file, parse frontmatter, and render body.
    Returns (metadata, rendered_prompt).
    """
    file_path = SKILLS_DIR / f"{name}.md"
    if not file_path.exists():
        # Try finding it recursively or just assume flat
        raise FileNotFoundError(f"Skill '{name}' not found at {file_path}")

    content = file_path.read_text(encoding="utf-8")

    # Simple frontmatter parsing
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter_raw = parts[1]
            body_template = parts[2]
            metadata = yaml.safe_load(frontmatter_raw) or {}
        else:
            metadata = {}
            body_template = content
    else:
        metadata = {}
        body_template = content

    # Validation
    required_vars = metadata.get("variables", [])
    if required_vars:
        missing = [v for v in required_vars if v not in args]
        if missing:
            raise ValueError(f"Missing required arguments for command '{name}': {missing}")

    # Rendering
    template = Template(body_template)
    # safe_substitute avoids crashing on missing variables not in the required list
    # strictly required variables are checked above.
    rendered = template.safe_substitute(args)

    return metadata, rendered.strip()
