from __future__ import annotations

from dataclasses import dataclass

from shared.models import Plan


@dataclass
class DispatchResult:
    """
    Result of routing a message.
    """

    request_id: str
    plan: Plan | None = None
    original_message: str = ""
    # If plan is None, we might need to specify why or what to do next (e.g. fallback to planner)
    # But for now, if plan is None, it means "Slow Path" (invoke Planner).
    # If plan is NOT None, it means "Fast Path" (execute this plan).
    # We might also want to pass metadata or "Skill" info if it was a skill.
    skill_name: str | None = None
