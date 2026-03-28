from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.models import AgentMessage, AgentRequest

from core.db import Context, Conversation
from core.runtime.config import Settings
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.memory import MemoryStore
from core.runtime.service import AgentService
from core.skills.registry import Skill
from core.tools import Tool, ToolRegistry, load_tool_registry
from core.tools.web_fetch import WebFetchTool


def _make_mock_skill_registry(skill_name: str = "mock_skill") -> MagicMock:
    """Create a mock SkillRegistry that returns a mock skill."""
    registry = MagicMock()
    skill = Skill(
        name=skill_name,
        path=Path("/mock/skills") / f"{skill_name}.md",
        description="Mock skill for testing",
        tools=[],
        model="agentchat",
        max_turns=3,
        body_template="Answer the user's question.",
    )
    registry.get.return_value = skill
    registry.get_skill_names.return_value = [skill_name]
    return registry


_PLAN_JSON = json.dumps(
    {
        "steps": [
            {
                "id": "skill-step",
                "label": "Run mock skill",
                "executor": "skill",
                "action": "skill",
                "tool": "mock_skill",
                "args": {},
            },
        ]
    }
)


class MockLiteLLMClient:
    async def generate(
        self,
        messages: list[AgentMessage] | list[dict[str, str]],
        model: str | None = None,
    ) -> str:
        return "ok"

    async def stream_chat(
        self,
        messages: list[AgentMessage] | list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        # Check if this is a planner call (contains planner system prompt marker)
        msgs = list(messages)
        system_content = ""
        for m in msgs:
            if isinstance(m, AgentMessage) and m.role == "system":
                system_content = m.content or ""
            elif isinstance(m, dict) and m.get("role") == "system":
                system_content = str(m.get("content", ""))
        if "PLANNER AGENT" in system_content or "You are the Planner Agent" in system_content:
            yield {"type": "content", "content": _PLAN_JSON}
        elif "SUPERVISOR" in system_content or "plan_supervisor" in system_content.lower():
            yield {"type": "content", "content": json.dumps({"decision": "ok", "issues": []})}
        else:
            yield {"type": "content", "content": "ok"}

    async def plan(
        self,
        messages: list[AgentMessage] | list[dict[str, str]],
        model: str | None = None,
    ) -> str:
        return _PLAN_JSON


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
        tools_config_path=tmp_path / "unused.yaml",
        tool_result_max_chars=100,
    )
    tool_registry = ToolRegistry([DummyTool()])
    skill_registry = _make_mock_skill_registry("mock_skill")
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=cast(MemoryStore, DummyMemory()),
        tool_registry=tool_registry,
        skill_registry=skill_registry,
    )

    request = AgentRequest(
        prompt="hello",
        metadata={
            "context_id": "00000000-0000-0000-0000-000000000001",
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
        id="00000000-0000-0000-0000-000000000001", default_cwd="/tmp"  # noqa: S108
    )
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result
    mock_ctx = MagicMock(
        id="00000000-0000-0000-0000-000000000001", default_cwd="/tmp"  # noqa: S108
    )

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
    assert any("TOOL OUTPUT" in (message.content or "") for message in system_messages)
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
    mock_fetcher.fetch.return_value = {
        "url": "https://example.com",
        "ok": False,
        "error": "boom",
    }

    with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
        tool = WebFetchTool(base_url="http://ignored")
        with pytest.raises(Exception) as exc:
            await tool.run("https://example.com")

    assert "boom" in str(exc.value)
