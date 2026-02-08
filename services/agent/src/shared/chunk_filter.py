"""Shared chunk filtering for platform adapters.

Applies verbosity-level and content-safety rules to AgentChunk streams.
Extracted from OpenWebUI adapter to enable reuse in Telegram and future adapters.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.content_classifier import contains_raw_model_tokens, is_noise_fragment
from shared.streaming import VerbosityLevel

LOGGER = logging.getLogger(__name__)


class ChunkFilter:
    """Filters AgentChunks based on verbosity level and safety rules."""

    def __init__(self, verbosity: VerbosityLevel = VerbosityLevel.DEFAULT) -> None:
        self.verbosity = verbosity
        self._shown_plan_descriptions: set[str] = set()

    def should_show(
        self, chunk_type: str, metadata: dict[str, Any] | None = None, content: str | None = None
    ) -> bool:
        """Determine if a chunk should be shown at the current verbosity level.

        VERBOSE/DEBUG: show everything.
        DEFAULT: show content, errors, trace_info, awaiting_input, select thinking/step_start.
        """
        if self.verbosity in (VerbosityLevel.VERBOSE, VerbosityLevel.DEBUG):
            return True

        # DEFAULT mode filtering
        # Always show: content (final answer), error, trace_info, awaiting_input
        if chunk_type in ("content", "error", "trace_info", "awaiting_input"):
            return True

        if chunk_type == "thinking":
            meta = metadata or {}
            source = meta.get("source", "")
            if source in ("reasoning_model", "skill_internal"):
                return False

            role = meta.get("role", "")
            if role == "Planner":
                return True
            if role == "Supervisor":
                if content and "replan" in content.lower():
                    return True
                return False

            orchestration = meta.get("orchestration", "")
            msg_type = meta.get("type", "")
            if orchestration == "plan" or msg_type == "plan":
                return True

            return False

        if chunk_type == "step_start":
            meta = metadata or {}
            executor = meta.get("executor", "")
            action = meta.get("action", "")
            if executor == "skill" or action == "skill":
                return True
            return False

        # Hide everything else in DEFAULT
        return False

    def is_safe_content(self, content: str) -> bool:
        """Return True if content passes safety filters (no raw tokens, not noise).

        Always filters raw model tokens regardless of verbosity.
        Filters noise fragments only in DEFAULT mode.
        """
        if contains_raw_model_tokens(content):
            LOGGER.debug("Filtered content with raw model tokens: %s", content[:100])
            return False

        if self.verbosity == VerbosityLevel.DEFAULT and is_noise_fragment(content):
            LOGGER.debug("Filtered noise fragment: %r", content)
            return False

        return True

    def is_duplicate_plan(self, content: str) -> bool:
        """Return True if this plan description has been seen before (dedup)."""
        plan_key = content[:100]
        if plan_key in self._shown_plan_descriptions:
            LOGGER.debug("Skipping duplicate plan: %s", plan_key[:50])
            return True
        self._shown_plan_descriptions.add(plan_key)
        return False
