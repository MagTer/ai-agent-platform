from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from core.core.litellm_client import LiteLLMClient
from shared.models import AgentMessage


class MockLLMClient(LiteLLMClient):
    """
    A mock LLM client that returns deterministic responses for testing.
    """

    def __init__(self, responses: list[str | dict[str, Any]] | None = None) -> None:
        # We don't call super().__init__ because we don't want a real httpx client
        self.responses = responses or []
        self.call_history: list[list[AgentMessage]] = []
        self._response_index = 0

    async def aclose(self) -> None:
        pass

    async def generate(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        """Return the next response from the queue."""
        self.call_history.append(list(messages))

        if self._response_index >= len(self.responses):
            # Default fallback if we run out of mocks
            return "Mock response: I have no more programmed responses."

        response = self.responses[self._response_index]
        self._response_index += 1

        if isinstance(response, dict):
            return json.dumps(response)
        return str(response)

    async def plan(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        """Mock the planning step."""
        return await self.generate(messages, model=model)

    async def list_models(self) -> Any:
        return {"data": [{"id": "mock-model"}]}
