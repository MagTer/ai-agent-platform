from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from agent.core.config import Settings
from agent.core.litellm_client import LiteLLMClient
from agent.core.memory import MemoryStore
from agent.core.models import AgentRequest
from agent.core.service import AgentService
from agent.tools import Tool, ToolRegistry, load_tool_registry


class MockLiteLLMClient:
    async def generate(self, messages):  # type: ignore[override]
        return "ok"


class DummyMemory:
    def search(self, query: str, limit: int = 5):
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
  type: agent.tools.web_fetch.WebFetchTool
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
