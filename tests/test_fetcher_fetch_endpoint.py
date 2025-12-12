"""Tests for the fetcher /fetch alias."""

from __future__ import annotations

import importlib
from typing import Any

from fastapi.testclient import TestClient


def _load_app(monkeypatch) -> Any:
    if "services.fetcher.app" in list(importlib.sys.modules.keys()):
        importlib.reload(importlib.import_module("services.fetcher.app"))
    app_module = importlib.import_module("services.fetcher.app")

    async def stub_fetch_and_extract(url: str) -> dict[str, Any]:
        return {"url": url, "ok": True, "text": "hello", "html": "<p>hello</p>"}

    monkeypatch.setattr(app_module, "fetch_and_extract", stub_fetch_and_extract, raising=True)
    return app_module


def test_fetch_alias_returns_single_item(monkeypatch):
    app_module = _load_app(monkeypatch)
    client = TestClient(app_module.app)

    response = client.post("/fetch", json={"url": "https://example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["item"]["url"] == "https://example.com"
    assert payload["item"]["text"] == "hello"
    assert payload["item"]["html"] == "<p>hello</p>"
