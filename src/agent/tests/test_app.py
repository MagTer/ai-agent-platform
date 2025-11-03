from __future__ import annotations

from pathlib import Path
from typing import cast

from agent.core.app import create_app
from agent.core.config import Settings
from agent.core.litellm_client import LiteLLMClient
from agent.core.memory import MemoryStore
from agent.core.service import AgentService
from fastapi.testclient import TestClient


class MockLiteLLMClient:
    async def generate(self, messages):  # type: ignore[override]
        sequence = list(messages)
        return "reply:" + sequence[-1].content


class DummyMemory:
    def __init__(self) -> None:
        self.persisted: list[str] = []

    def search(self, query: str, limit: int = 5):  # noqa: D401
        return []

    def add_records(self, records):
        for record in records:
            self.persisted.append(record.text)


def build_service(tmp_path: Path) -> AgentService:
    settings = Settings(sqlite_state_path=tmp_path / "state.sqlite")
    return AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=cast(MemoryStore, DummyMemory()),
    )


def test_chat_completions_roundtrip(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    app = create_app(service._settings, service=service)  # type: ignore[arg-type]
    client = TestClient(app)

    payload = {
        "model": "agent-model",
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "Hello"},
        ],
        "metadata": {"tools": []},
    }

    response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"].startswith("reply:")
    assert data["metadata"]["tools"] == []
    assert isinstance(data["steps"], list)
    assert data["metadata"]["steps"] == data["steps"]
    assert data["choices"][0]["message"]["metadata"]["steps"] == data["steps"]

    follow_payload = {
        "model": "agent-model",
        "conversation_id": data["id"],
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": data["choices"][0]["message"]["content"],
            },
            {"role": "user", "content": "How are you?"},
        ],
    }

    second = client.post("/v1/chat/completions", json=follow_payload)
    assert second.status_code == 200
    follow_data = second.json()
    assert follow_data["id"] == data["id"]
    assert follow_data["choices"][0]["message"]["content"].startswith("reply:")
    assert follow_data["metadata"]["steps"] == follow_data["steps"]
