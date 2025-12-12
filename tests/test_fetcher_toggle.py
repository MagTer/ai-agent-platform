import importlib

import pytest


async def make_stubbed_app(monkeypatch, enable_qdrant: bool):
    # Toggle via env then reload module so globals are re-read
    monkeypatch.setenv("ENABLE_QDRANT", "true" if enable_qdrant else "false")
    if "services.fetcher.app" in list(importlib.sys.modules.keys()):
        importlib.reload(importlib.import_module("services.fetcher.app"))
    app = importlib.import_module("services.fetcher.app")

    # Stub dependencies to avoid network
    async def stub_search(q: str, k: int = 5, lang: str = "sv"):
        return {
            "results": [
                {"url": "https://web.example/1"},
                {"url": "https://web.example/2"},
            ]
        }

    async def stub_fetch_and_extract(url: str):
        return {"ok": True, "url": url, "text": f"content for {url}"}

    # For the qdrant path, either provide memory docs or raise if called
    if enable_qdrant:

        async def stub_qdrant_query(q: str, top_k: int = 5):
            return [{"ok": True, "url": "https://mem.example/1", "text": "memory"}]

    else:

        async def stub_qdrant_query(q: str, top_k: int = 5):
            raise AssertionError("qdrant_query should not be called when ENABLE_QDRANT=false")

    async def stub_summarize(model, query, urls, items, lang):
        return "ok"

    monkeypatch.setattr(app, "search", stub_search, raising=True)
    monkeypatch.setattr(app, "fetch_and_extract", stub_fetch_and_extract, raising=True)
    monkeypatch.setattr(app, "qdrant_query", stub_qdrant_query, raising=True)
    monkeypatch.setattr(app, "summarize_with_litellm", stub_summarize, raising=True)
    return app


@pytest.mark.asyncio
async def test_research_uses_memory_when_enabled(monkeypatch):
    app = await make_stubbed_app(monkeypatch, enable_qdrant=True)
    result = await app._research_core("test", 2, None, "sv")
    assert "sources" in result
    # Memory URL should be present and ordered first
    assert result["sources"][0] == "https://mem.example/1"


@pytest.mark.asyncio
async def test_research_skips_memory_when_disabled(monkeypatch):
    app = await make_stubbed_app(monkeypatch, enable_qdrant=False)
    result = await app._research_core("test", 2, None, "sv")
    assert "sources" in result
    assert all(not s.startswith("https://mem.example/") for s in result["sources"])
