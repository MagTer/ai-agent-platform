"""Structured intent classification using Pydantic models.

This module implements LangGraph-style structured output routing
for classifying user intent and determining the appropriate handler.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from core.core.litellm_client import LiteLLMClient

LOGGER = logging.getLogger(__name__)


class IntentClassification(BaseModel):
    """Structured output for intent classification.

    This model replaces free-text CHAT/TASK string parsing with
    type-safe, validated routing decisions.
    """

    route: Literal["chat", "agentic", "skill"] = Field(
        description=(
            "chat = simple Q&A, greetings, no tools needed. "
            "agentic = requires tools, research, planning. "
            "skill = explicit slash command detected."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0",
    )
    detected_skill: str | None = Field(
        default=None,
        description="If route=skill, the detected skill name (without slash)",
    )
    reasoning: str = Field(
        description="Brief explanation for the routing decision",
    )


# System prompt for the classifier
CLASSIFIER_SYSTEM_PROMPT = (
    "You are an intent classifier. Analyze user input and return a JSON object.\n\n"
    "CLASSIFICATION RULES:\n"
    '1. "chat" - Simple greetings, chit-chat, questions about yourself, '
    "general knowledge you know with certainty\n"
    '2. "agentic" - Requires tools, research, file operations, current data, '
    "anything time-sensitive\n"
    '3. "skill" - User explicitly used a slash command '
    "(e.g., /researcher, /requirements_engineer)\n\n"
    'AGENTIC TRIGGERS (always return "agentic"):\n'
    "- Words: check, verify, search, research, find, look up, analyze, "
    "create, write, read, run\n"
    '- Phrases: "what is the latest", "current", "today", "now", "recent"\n'
    "- File operations: any mention of files, code, logs, tests\n"
    '- Tool implications: "check logs", "run tests", "search for"\n\n'
    'CHAT INDICATORS (return "chat" only if confident):\n'
    "- Pure greetings: hi, hello, hey, good morning\n"
    "- Identity questions: who are you, what can you do\n"
    '- General knowledge: capital of France, translate "hello" to Spanish\n\n'
    "OUTPUT FORMAT (strict JSON only):\n"
    "{\n"
    '  "route": "chat" | "agentic" | "skill",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "detected_skill": "skill_name" or null,\n'
    '  "reasoning": "brief explanation"\n'
    "}\n"
)


class IntentClassifier:
    """Classify user intent using structured LLM output."""

    def __init__(self, litellm: LiteLLMClient) -> None:
        self._litellm = litellm

    async def classify(self, message: str) -> IntentClassification:
        """Classify a user message and return structured intent.

        Args:
            message: The user's input message

        Returns:
            IntentClassification with route, confidence, and reasoning
        """
        from shared.models import AgentMessage

        # Check for explicit slash command first (no LLM needed)
        stripped = message.strip()
        if stripped.startswith("/"):
            parts = stripped.split(maxsplit=1)
            skill_name = parts[0][1:]  # Remove '/'
            return IntentClassification(
                route="skill",
                confidence=1.0,
                detected_skill=skill_name,
                reasoning=f"Explicit slash command detected: /{skill_name}",
            )

        # Use LLM for classification
        try:
            response = await self._litellm.generate(
                messages=[
                    AgentMessage(role="system", content=CLASSIFIER_SYSTEM_PROMPT),
                    AgentMessage(role="user", content=stripped),
                ],
            )

            # Parse JSON response
            result = self._parse_response(response)
            LOGGER.info(
                "Intent classified: route=%s confidence=%.2f reason=%s",
                result.route,
                result.confidence,
                result.reasoning,
            )
            return result

        except Exception as e:
            LOGGER.warning("Intent classification failed: %s, defaulting to agentic", e)
            return IntentClassification(
                route="agentic",
                confidence=0.5,
                detected_skill=None,
                reasoning=f"Classification failed ({e}), defaulting to agentic",
            )

    def _parse_response(self, response: str) -> IntentClassification:
        """Parse LLM response into IntentClassification."""
        # Try direct JSON parse
        try:
            data = json.loads(response.strip())
            return IntentClassification(**data)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from response
        start = response.find("{")
        end = response.rfind("}")
        if start != -1 and end != -1:
            try:
                fragment = response[start : end + 1]
                data = json.loads(fragment)
                return IntentClassification(**data)
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: detect route from keywords
        lower = response.lower()
        if "agentic" in lower or "task" in lower:
            route: Literal["chat", "agentic", "skill"] = "agentic"
        elif "chat" in lower:
            route = "chat"
        else:
            route = "agentic"

        return IntentClassification(
            route=route,
            confidence=0.6,
            detected_skill=None,
            reasoning="Parsed from non-JSON response",
        )


__all__ = ["IntentClassification", "IntentClassifier", "CLASSIFIER_SYSTEM_PROMPT"]
