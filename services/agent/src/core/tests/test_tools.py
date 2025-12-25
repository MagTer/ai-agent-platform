from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.models import AgentMessage, AgentRequest

from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.service import AgentService
from core.db import Context, Conversation
from core.tools import Tool, ToolRegistry, load_tool_registry
from core.tools.web_fetch import WebFetchTool


class MockLiteLLMClient:
    async def generate(
        self,
        messages: list[AgentMessage] | list[dict[str, str]],
        model: str | None = None,
    ) -> str:
        return "ok"

    async def plan(
        self,
        messages: list[AgentMessage] | list[dict[str, str]],
        model: str | None = None,
    ) -> str:
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
    def __init__(self) -> None:
        self.persisted: list[str] = []

    async def ainit(self) -> None:
        pass

    async def search(
        self, query: str, limit: int = 5, conversation_id: str | None = None
    ) -> list[Any]:
        return []

    async def add_records(self, records: list[Any]) -> None:
        for record in records:
            self.persisted.append(record.text)


class DummyTool(Tool):
    name = "dummy"
    description = "Dummy tool for testing"

    async def run(self, text: str) -> str:
        return text.upper()


def test_load_tool_registry_registers_tools(tmp_path: Path) -> None:
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


def test_load_tool_registry_handles_missing_file(tmp_path: Path) -> None:
    config = tmp_path / "missing.yaml"
    registry = load_tool_registry(config)
    assert registry.available() == []


@pytest.mark.asyncio
async def test_agent_service_executes_tool(tmp_path: Path) -> None:
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

    # Mock Session
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock(
        id="default-ctx", default_cwd="/tmp"  # noqa: S108
    )
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result
    mock_ctx = MagicMock(id="default-ctx", default_cwd="/tmp")  # noqa: S108

    def get_side_effect(model: Any, id: Any) -> Any:
        if model == Conversation:
            return None
        if model == Context:
            return mock_ctx
        return None

    session.get.side_effect = get_side_effect

    response = await service.handle_request(request, session=session)

    assert response.metadata["tool_results"][0]["status"] == "ok"
    assert "TOOL OUTPUT" in response.metadata["tool_results"][0]["output"]
    system_messages = [message for message in response.messages if message.role == "system"]
    assert any("TOOL OUTPUT" in message.content for message in system_messages)
    assert any(
        step.get("type") == "tool" and step.get("name") == "dummy" for step in response.steps
    )


@pytest.mark.asyncio
async def test_web_fetch_tool_parses_fetcher_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch.return_value = {
        "url": "https://example.com",
        "ok": True,
        "text": "This is a long block of extracted text that should be truncated.",
        "html_truncated": "<html><body>Hello world</body></html>",
    }

    # Patch get_fetcher in the tool module
    with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
        tool = WebFetchTool(
            base_url="http://ignored",
            include_html=True,
            summary_max_chars=32,
            html_max_chars=32,
        )
        output = await tool.run("https://example.com")

        assert "Fetched URL: https://example.com" in output
        assert "Extracted Text Snippet:" in output
        assert "Raw HTML Snippet:" in output
        assert "This is a long block of extracte" in output
        assert "…" in output


@pytest.mark.asyncio
async def test_web_fetch_tool_raises_on_error_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch.return_value = {"url": "https://example.com", "ok": False, "error": "boom"}

    with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
        tool = WebFetchTool(base_url="http://ignored")
        with pytest.raises(Exception) as exc:
            await tool.run("https://example.com")

    assert "boom" in str(exc.value)
