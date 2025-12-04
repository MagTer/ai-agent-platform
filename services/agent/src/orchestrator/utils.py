import re
from typing import Any

from .skill_loader import Skill


def render_skill_prompt(skill: Skill, params: dict[str, Any]) -> str:
    """
    Renders the skill's prompt template by substituting placeholders with values from params.

    Args:
        skill: The Skill object containing the prompt_template.
        params: A dictionary of parameters to substitute into the template.

    Returns:
        The rendered prompt string.
    """
    template = skill.prompt_template

    def replace_match(match):
        key = match.group(1)
        # Return the value from params if it exists, else empty string
        # We could also leave the placeholder if we wanted, but instructions say "default to empty"
        return str(params.get(key, ""))

    # Regex to match {{variable}}
    # We assume variable names are alphanumeric + underscore
    rendered_prompt = re.sub(r"\{\{([a-zA-Z0-9_]+)\}\}", replace_match, template)
    return rendered_prompt
