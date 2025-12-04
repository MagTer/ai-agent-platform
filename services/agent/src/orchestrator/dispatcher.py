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

                parameters = {}
                if args_str:
                    # Simple parameter parsing logic
                    parsed_as_kv = False
                    # If it looks like key=value pairs
                    if "=" in args_str:
                        try:
                            # Naive split by space. Doesn't handle quotes for now.
                            parts_args = args_str.split()
                            temp_params = {}
                            all_kv = True
                            for p in parts_args:
                                if "=" not in p:
                                    all_kv = False
                                    break
                                k, v = p.split("=", 1)
                                temp_params[k] = v

                            if all_kv:
                                parameters = temp_params
                                parsed_as_kv = True
                        except Exception:
                            parsed_as_kv = False

                    # Fallback: assign entire string to the first input
                    if not parsed_as_kv:
                        if skill.inputs:
                            first_input_name = skill.inputs[0].name
                            parameters[first_input_name] = args_str
                        else:
                            # Skill has no inputs defined, but args provided.
                            # Store in generic 'args' just in case.
                            parameters["args"] = args_str

                return SkillExecutionRequest(
                    skill=skill,
                    parameters=parameters,
                    session_id=session_id,
                    original_message=message,
                )
            else:
                LOGGER.warning(
                    f"Command '/{command}' not found in skills. Falling back to chat."
                )
                # Fallback to chat if command not found, or maybe return error?
                # Target state implies routing logic. We'll treat unknown commands as Chat for now
                # or we could return a System Message.
                # Let's stick to GeneralChatRequest as per instructions
                # "If no match: return GeneralChatRequest"
                pass

        return GeneralChatRequest(message=message, session_id=session_id)
