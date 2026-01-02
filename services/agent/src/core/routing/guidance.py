"""Routing guidance patterns extracted from planner.

This module centralizes the ROUTING GUIDANCE that helps the planner
decide between direct answers and research-required responses.
"""

from __future__ import annotations

DIRECT_ANSWER_PATTERNS = """
**DIRECT ANSWER (single completion step, NO research needed)**:
- Translations: 'What is bicycle in Spanish?' → 'bicicleta'
- Definitions: 'What does API stand for?'
- Basic facts YOU KNOW: 'What is the capital of France?'
- Simple math: 'What is 15% of 200?'
- Programming syntax: 'How to write a for loop in Python?'
- General knowledge from your training data
"""

RESEARCH_REQUIRED_PATTERNS = """
**RESEARCH REQUIRED (use researcher skill)**:
- Current events: 'What are the latest AI developments?'
- Real-time data: Stock prices, weather, sports scores
- Specific statistics: 'What is the population of Tokyo in 2025?'
- Unknown or uncertain facts: If you're not 95%+ confident
- Recent releases: Software versions, product launches since your training
"""


def build_routing_guidance(available_skills: list[str] | None = None) -> str:
    """Build the routing guidance section for the planner prompt.

    Args:
        available_skills: Optional list of available skill names for context

    Returns:
        Formatted routing guidance string
    """
    skill_context = ""
    if available_skills:
        skill_names = ", ".join(available_skills)
        skill_context = f"\nAvailable skills for delegation: {skill_names}\n"

    return f"""### ROUTING GUIDANCE (CRITICAL)
Before creating a plan, determine if the request needs RESEARCH or a DIRECT ANSWER:

{DIRECT_ANSWER_PATTERNS}
{RESEARCH_REQUIRED_PATTERNS}
{skill_context}"""


__all__ = [
    "DIRECT_ANSWER_PATTERNS",
    "RESEARCH_REQUIRED_PATTERNS",
    "build_routing_guidance",
]
