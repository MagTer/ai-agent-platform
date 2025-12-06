from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
import respx
from httpx import Response

from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.models import AgentRequest
from core.core.service import AgentService
from core.tools import Tool, ToolRegistry, load_tool_registry
from core.tools.web_fetch import WebFetchTool


class MockLiteLLMClient:
    async def generate(self, messages):  # type: ignore[override]
        return "ok"

    async def plan(self, messages):  # type: ignore[override]
        return json.dumps(
            {
                "steps": [
                    {
                        "id": "memory",
                        "label": "Fetch memories",
                        "executor": "agent",
                        "action": "memory",
                    },
                    {
                        "id": "completion",
                        "label": "Compose reply",
                        "executor": "litellm",
                        "action": "completion",
                    },
                ]
            }
        )


class DummyMemory:
    def search(self, query: str, limit: int = 5, conversation_id: str | None = None):
        return []

    def add_records(self, records):
        return None


class DummyTool(Tool):
    name = "dummy"
    description = "Dummy tool for testing"

    async def run(self, text: str) -> str:  # type: ignore[override]
        return text.upper()


def test_load_tool_registry_registers_tools(tmp_path: Path):
    config = tmp_path / "tools.yaml"
    config.write_text(
        """
- name: test
  type: core.tools.web_fetch.WebFetchTool
  args:
    base_url: http://webfetch:8081
""",
        encoding="utf-8",
    )

    registry = load_tool_registry(config)

    assert "test" in registry.available()


def test_load_tool_registry_handles_missing_file(tmp_path: Path):
    config = tmp_path / "missing.yaml"
    registry = load_tool_registry(config)
    assert registry.available() == []


@pytest.mark.asyncio
async def test_agent_service_executes_tool(tmp_path: Path):
    settings = Settings(
        sqlite_state_path=tmp_path / "state.sqlite",
        tools_config_path=tmp_path / "unused.yaml",
        tool_result_max_chars=100,
    )
    tool_registry = ToolRegistry([DummyTool()])
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=cast(MemoryStore, DummyMemory()),
        tool_registry=tool_registry,
    )

    request = AgentRequest(
        prompt="hello",
        metadata={
            "tools": ["dummy"],
            "tool_calls": [
                {
                    "name": "dummy",
                    "args": {"text": "tool output"},
                }
            ],
        },
    )

    response = await service.handle_request(request)

    assert response.metadata["tool_results"][0]["status"] == "ok"
    assert "TOOL OUTPUT" in response.metadata["tool_results"][0]["output"]
    system_messages = [message for message in response.messages if message.role == "system"]
    assert any("TOOL OUTPUT" in message.content for message in system_messages)
    assert any(
        step.get("type") == "tool" and step.get("name") == "dummy" for step in response.steps
    )


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_tool_parses_fetcher_response():
    tool = WebFetchTool(
        base_url="http://fetcher:8081",
        include_html=True,
        summary_max_chars=32,
        html_max_chars=32,
    )
    respx.post("http://fetcher:8081/fetch").mock(
        return_value=Response(
            200,
            json={
                "item": {
                    "url": "https://example.com",
                    "ok": True,
                    "text": "This is a long block of extracted text that should be truncated.",
                    "html": "<html><body>Hello world</body></html>",
                }
            },
        )
    )

    output = await tool.run("https://example.com")

    assert "Fetched URL: https://example.com" in output
    assert "Extracted Text Snippet:" in output
    assert "Raw HTML Snippet:" in output
    assert "This is a long block of extracte" in output
    assert "…" in output


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_tool_raises_on_error_response():
    tool = WebFetchTool(base_url="http://fetcher:8081")
    respx.post("http://fetcher:8081/fetch").mock(
        return_value=Response(
            200,
            json={"item": {"url": "https://example.com", "ok": False, "error": "boom"}},
        )
    )

    with pytest.raises(Exception) as exc:
        await tool.run("https://example.com")

    assert "boom" in str(exc.value)
