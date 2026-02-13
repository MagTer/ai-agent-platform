from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from unittest.mock import MagicMock

from shared.models import AgentMessage

from core.core.litellm_client import LiteLLMClient


class MockLLMClient(LiteLLMClient):
    """
    A mock LLM client that returns deterministic responses for testing.
    """

    responses: list[str | dict[str, Any]]
    call_history: list[list[AgentMessage]]

    def __init__(self, responses: list[str | dict[str, Any]] | None = None) -> None:
        # We don't call super().__init__ because we don't want a real httpx client
        self.responses = responses or []
        self.call_history = []
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

    async def stream_chat(
        self,
        messages: Iterable[AgentMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Stream the next response from queue as a single chunk."""
        content = await self.generate(messages)
        yield {
            "type": "content",
            "content": content,
            "tool_call": None,
            "metadata": None,
        }


class InMemoryAsyncSession:
    """
    In-memory mock for SQLAlchemy AsyncSession for unit testing without a database.

    Supports basic CRUD operations with an in-memory store.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[type, Any], Any] = {}
        self._pending: list[Any] = []
        self._deleted: list[Any] = []

    async def get(self, model: type, id: Any) -> Any:
        """Retrieve an object by model type and ID."""
        return self._store.get((model, id))

    def add(self, obj: Any) -> None:
        """Add an object to the pending list."""
        self._pending.append(obj)

    async def delete(self, obj: Any) -> None:
        """Mark an object for deletion."""
        self._deleted.append(obj)

    async def commit(self) -> None:
        """Commit pending changes to the in-memory store."""
        for obj in self._pending:
            # Extract ID - assume it has an 'id' attribute
            obj_id = getattr(obj, "id", id(obj))
            obj_type = type(obj)
            self._store[(obj_type, obj_id)] = obj

        for obj in self._deleted:
            obj_id = getattr(obj, "id", id(obj))
            obj_type = type(obj)
            self._store.pop((obj_type, obj_id), None)

        self._pending.clear()
        self._deleted.clear()

    async def rollback(self) -> None:
        """Discard pending changes."""
        self._pending.clear()
        self._deleted.clear()

    async def refresh(self, obj: Any) -> None:
        """Refresh an object (no-op for in-memory)."""
        pass

    async def execute(self, statement: Any) -> Any:
        """Execute a statement (returns mock result)."""
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        result.scalars.return_value.first.return_value = None
        return result

    async def close(self) -> None:
        """Close the session (no-op)."""
        pass

    async def __aenter__(self) -> InMemoryAsyncSession:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
