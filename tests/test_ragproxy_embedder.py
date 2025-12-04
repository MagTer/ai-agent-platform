from __future__ import annotations

from types import SimpleNamespace

from ragproxy import chat_completions, qdrant_retrieve


class DummyResponse(SimpleNamespace):
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return getattr(self, "_data", {})


def test_qdrant_retrieve_round_trips_embedder(monkeypatch):
    calls: list[str] = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        if url.startswith("http://embedder"):
            return DummyResponse(_data={"vectors": [[0.3, 0.4]]})
        if url.startswith("http://qdrant"):
            return DummyResponse(
                _data={
                    "result": [
                        {
                            "payload": {
                                "url": "https://example.com",
                                "text": "context",
                            },
                            "vector": [0.3, 0.4],
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("ragproxy.requests.post", fake_post)
    hits = qdrant_retrieve("hello", 2)
    assert len(hits) == 1
    assert hits[0]["url"] == "https://example.com"
    assert any("embedder" in call for call in calls)


def test_chat_completions_injects_rag_context(monkeypatch):
    seen_payload: dict | None = None

    def fake_post(url, json=None, timeout=None):
        nonlocal seen_payload
        if url.startswith("http://embedder"):
            return DummyResponse(_data={"vectors": [[0.1, 0.2]]})
        if url.startswith("http://qdrant"):
            return DummyResponse(
                _data={
                    "result": [
                        {
                            "payload": {
                                "url": "https://docs.example",
                                "text": "doc text",
                            },
                            "vector": [0.1, 0.2],
                        }
                    ]
                }
            )
        if url.startswith("http://litellm"):
            seen_payload = json or {}
            return DummyResponse(
                _data={
                    "choices": [{"message": {"role": "assistant", "content": "reply"}}]
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("ragproxy.requests.post", fake_post)
    result = chat_completions(
        {
            "model": "rag/phi3-en",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is new?"},
            ],
        }
    )
    assert result["choices"][0]["message"]["content"] == "reply"
    assert seen_payload is not None
    user_messages = [
        msg for msg in seen_payload["messages"] if msg.get("role") == "user"
    ]
    assert any("Context:" in msg.get("content", "") for msg in user_messages)
