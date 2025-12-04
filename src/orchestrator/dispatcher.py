import logging
from dataclasses import dataclass, field

from .skill_loader import Skill, SkillLoader

LOGGER = logging.getLogger(__name__)


@dataclass
class SkillExecutionRequest:
    skill: Skill
    parameters: dict[str, str] = field(default_factory=dict)
    session_id: str = ""
    original_message: str = ""


@dataclass
class GeneralChatRequest:
    message: str
    session_id: str = ""


class Dispatcher:
    def __init__(self, skill_loader: SkillLoader):
        self.skill_loader = skill_loader
        # Ensure skills are loaded
        if not self.skill_loader.skills:
            self.skill_loader.load_skills()

    def route_message(
        self, session_id: str, message: str
    ) -> SkillExecutionRequest | GeneralChatRequest:
        """
        Routes a user message to either a Skill or General Chat.

        Logic:
        - If message starts with '/', attempt to match a skill name.
        - Otherwise, treat as general chat.
        """
        stripped_message = message.strip()

        if stripped_message.startswith("/"):
            # Extract command name
            parts = stripped_message.split(" ", 1)
            command = parts[0][1:]  # Remove '/'
            args_str = parts[1] if len(parts) > 1 else ""

            skill = self.skill_loader.skills.get(command)

            if skill:
                LOGGER.info(f"Routing to skill: {skill.name}")
                # Basic parameter parsing (implementation detail: could be improved)
                # For now, we treat the rest of the string as a generic 'input' or empty
                # In a real scenario, we might parse key=value or positional args
                parameters = {"args": args_str}

                return SkillExecutionRequest(
                    skill=skill,
                    parameters=parameters,
                    session_id=session_id,
                    original_message=message,
                )
            else:
                LOGGER.warning(f"Command '/{command}' not found in skills. Falling back to chat.")
                # Fallback to chat if command not found, or maybe return error?
                # Target state implies routing logic. We'll treat unknown commands as Chat for now
                # or we could return a System Message.
                # Let's stick to GeneralChatRequest as per instructions
                # "If no match: return GeneralChatRequest"
                pass

        return GeneralChatRequest(message=message, session_id=session_id)
