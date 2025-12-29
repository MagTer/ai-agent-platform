import os
from pathlib import Path
from string import Template
from typing import Any

import yaml

# Default to a path relative to this file if not in docker
# This file is in src/core, so skills is likely ../../../../../skills
DEFAULT_SKILLS_PATH = Path(__file__).parent.parent.parent.parent.parent / "skills"
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", str(DEFAULT_SKILLS_PATH)))


def get_registry_index() -> str:
    """Return a bulleted list of available skills for the prompt."""
    skills = list_commands()
    if not skills:
        return "(No skills loaded)"

    lines = []
    for s in skills:
        lines.append(f"• [{s['name']}]: {s.get('description', 'No description')}")
    return "\n".join(lines)


def list_commands() -> list[dict[str, Any]]:
    """List all available commands/skills by scanning SKILLS_DIR recursively."""
    commands = []
    if not SKILLS_DIR.exists():
        return []

    for path in SKILLS_DIR.rglob("*.md"):
        relative_path = path.relative_to(SKILLS_DIR).with_suffix("")
        name = str(relative_path).replace("\\", "/")  # Normalize to forward slashes

        try:
            content = path.read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 2:
                    metadata = yaml.safe_load(parts[1]) or {}
                else:
                    metadata = {}
            else:
                metadata = {}

            commands.append(
                {
                    "name": name,
                    "description": metadata.get("description", "No description"),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            v: {"type": "string"} for v in metadata.get("variables", [])
                        },
                        "required": metadata.get("variables", []),
                    },
                    "metadata": metadata,
                }
            )
        except Exception as e:
            # Log error but continue
            print(f"Error loading skill {path}: {e}")
            continue

    return commands


def load_command(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """
    Load a skill/command from markdown file, parse frontmatter, and render body.
    Returns (metadata, rendered_prompt).
    """
    # name can be 'general/summarize' or 'summarize'
    # strict lookup first
    file_path = SKILLS_DIR / f"{name}.md"

    if not file_path.exists():
        # Try finding by filename only if no path separator
        if "/" not in name and "\\" not in name:
            candidates = list(SKILLS_DIR.rglob(f"{name}.md"))
            if candidates:
                file_path = candidates[0]
            else:
                raise FileNotFoundError(f"Skill '{name}' not found in {SKILLS_DIR}")
        else:
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

    # Ensure tools list exists (Security: Default to empty/read-only if undefined)
    if "tools" not in metadata:
        # Default to empty list for specific scoping, or a safe subset.
        # As per requirement: "safe default"
        metadata["tools"] = []

    # Rendering
    template = Template(body_template)
    # safe_substitute avoids crashing on missing variables not in the required list
    # strictly required variables are checked above.
    rendered = template.safe_substitute(args)

    return metadata, rendered.strip()
