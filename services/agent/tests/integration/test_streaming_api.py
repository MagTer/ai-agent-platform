import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from shared.models import AgentRequest

from core.db.engine import get_db
from interfaces.http.app import create_app

# Mock Data
MOCK_STEP_ID = "step-123"
MOCK_TOKENS = ["Hello", " ", "World", "!"]


class MockAgentService:
    """Mock service that simulates a streaming execution."""

    async def execute_stream(
        self,
        request: AgentRequest,
        conversation_id: str | None = None,
        session: Any = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        # Yield a step start event
        yield {
            "type": "step_start",
            "content": "Thinking process",
            "metadata": {"id": MOCK_STEP_ID, "label": "Planning"},
        }

        # Yield content tokens
        for token in MOCK_TOKENS:
            yield {"type": "content", "content": token}

        # Yield completion result
        yield {
            "type": "completion",
            "content": "".join(MOCK_TOKENS),
            "model": "mock-model",
        }


@pytest.fixture
def test_client():
    """Create a TestClient with mocked dependencies."""
    # Create mock service
    mock_service = MockAgentService()

    # Pass mock service to create_app (it will be stored in app.state.test_service)
    app = create_app(service=mock_service)

    # Override DB session to avoid connection errors
    mock_session = AsyncMock()
    app.dependency_overrides[get_db] = lambda: mock_session

    with TestClient(app) as client:
        yield client


def test_streaming_endpoint(test_client: TestClient):
    """
    Verify that /v1/chat/completions returns a proper Event Stream
    with the expected chunk types (step_start, content).
    """
    payload = {
        "model": "agent-model",
        "messages": [{"role": "user", "content": "Test prompt"}],
        "stream": True,  # Important: Request streaming
    }

    response = test_client.post("/v1/chat/completions", json=payload)

    # 1. Check Headers (SSE)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    # 2. Parse SSE lines
    lines = response.text.strip().split("\n")
    data_lines = [line for line in lines if line.startswith("data: ")]

    assert len(data_lines) > 0

    tokens_received = []
    has_step = False

    for line in data_lines:
        json_str = line.removeprefix("data: ").strip()
        if json_str == "[DONE]":
            continue

        try:
            chunk = json.loads(json_str)
        except json.JSONDecodeError:
            continue

        # OpenWebUI Adapter format structure:
        # It maps agent events to OpenAI-like chunks.
        # Check delta content

        choices = chunk.get("choices", [])
        if not choices:
            continue

        delta = choices[0].get("delta", {})
        content = delta.get("content", "")

        # In our adapter:
        # step_start -> "> 👣 **Plan:** *Thinking process*\n\n"
        # content -> raw content

        if "👣 **Plan:**" in content:
            has_step = True
        elif content in MOCK_TOKENS:
            tokens_received.append(content)

    # 3. Verify content
    assert has_step, "Did not receive formatted Plan step"
    # Note: Adapter might combine or buffer, but we expect at least some tokens
    # Our mock sends exact tokens. The adapter wraps them using `_format_chunk`.
    # Let's just check if we got the text "Hello"

    full_text = "".join(tokens_received)
    # The adapter doesn't mess with raw content tokens, so they should appear cleanly
    # BUT, our MockAgentService yields "content" type.
    # OpenWebUIAdapter: if chunk_type == "content": yield _format_chunk(..., content)

    assert "Hello" in tokens_received or "Hello" in full_text
    assert "World" in tokens_received or "World" in full_text
