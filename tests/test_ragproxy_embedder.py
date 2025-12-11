from __future__ import annotations

import json  # Added import
from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from services.ragproxy import chat_completions, qdrant_retrieve


class DummyResponse(SimpleNamespace):
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return getattr(self, "_data", {})


@pytest.mark.asyncio
@respx.mock
async def test_qdrant_retrieve_round_trips_embedder():
    respx.post("http://embedder:8082/embed").mock(
        return_value=Response(200, json={"vectors": [[0.3, 0.4]]})
    )
    respx.post("http://qdrant:6333/collections/memory/points/search").mock(
        return_value=Response(
            200,
            json={
                "result": [
                    {
                        "payload": {"url": "https://example.com", "text": "context"},
                        "vector": [0.3, 0.4],
                    }
                ]
            },
        )
    )

    hits = await qdrant_retrieve("hello", 2)
    assert len(hits) == 1
    assert hits[0]["url"] == "https://example.com"


@pytest.mark.asyncio
@respx.mock
async def test_chat_completions_injects_rag_context():
    seen_payload: dict | None = None

    async def capture_payload(request):
        nonlocal seen_payload
        seen_payload = json.loads(request.content.decode())
        return Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "reply"}}]},
        )

    respx.post("http://embedder:8082/embed").mock(
        return_value=Response(200, json={"vectors": [[0.1, 0.2]]})
    )
    respx.post("http://qdrant:6333/collections/memory/points/search").mock(
        return_value=Response(
            200,
            json={
                "result": [
                    {
                        "payload": {"url": "https://docs.example", "text": "doc text"},
                        "vector": [0.1, 0.2],
                    }
                ]
            },
        )
    )
    respx.post("http://litellm:4000/v1/chat/completions").mock(side_effect=capture_payload)

    result = await chat_completions(
        {
            "model": "rag/llama3-en",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is new?"},
            ],
        }
    )
    assert result["choices"][0]["message"]["content"] == "reply"
    assert seen_payload is not None
    user_messages = [msg for msg in seen_payload["messages"] if msg.get("role") == "user"]
    assert any("Context:" in msg.get("content", "") for msg in user_messages)
