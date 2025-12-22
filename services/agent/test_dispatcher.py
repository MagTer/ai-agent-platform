from unittest.mock import AsyncMock, MagicMock

import pytest
from src.orchestrator.dispatcher import Dispatcher, RoutingDecision


@pytest.fixture
def mock_skill_loader():
    loader = MagicMock()
    loader.skills = {}
    return loader


@pytest.fixture
def mock_litellm():
    client = MagicMock()
    client.generate = AsyncMock()
    return client


@pytest.fixture
def dispatcher(mock_skill_loader, mock_litellm):
    return Dispatcher(mock_skill_loader, mock_litellm)


@pytest.mark.asyncio
async def test_route_message_fast_path_slash_command(dispatcher, mock_skill_loader):
    # Setup
    mock_skill = MagicMock()
    mock_skill.name = "TestSkill"
    mock_skill.tools = ["tool1"]
    mock_skill_loader.skills = {"test": mock_skill}

    # Execute
    result = await dispatcher.route_message("session-1", "/test do something")

    # Verify
    assert result.decision == RoutingDecision.FAST_PATH
    assert result.skill_name == "TestSkill"
    assert result.metadata["tools"] == ["tool1"]


@pytest.mark.asyncio
async def test_route_message_fast_path_regex(dispatcher):
    # Execute
    result = await dispatcher.route_message("session-1", "tänd lampan")

    # Verify
    assert result.decision == RoutingDecision.FAST_PATH
    assert result.plan is not None
    assert result.plan.steps[0].tool == "home_automation"


@pytest.mark.asyncio
async def test_route_message_chat_intent(dispatcher, mock_litellm):
    # Setup
    mock_litellm.generate.return_value = "CHAT"

    # Execute
    result = await dispatcher.route_message("session-1", "Hello there")

    # Verify
    assert result.decision == RoutingDecision.CHAT
    mock_litellm.generate.assert_called_once()


@pytest.mark.asyncio
async def test_route_message_agentic_intent(dispatcher, mock_litellm):
    # Setup
    mock_litellm.generate.return_value = "TASK"

    # Execute
    result = await dispatcher.route_message("session-1", "Research AI agents")

    # Verify
    assert result.decision == RoutingDecision.AGENTIC
    mock_litellm.generate.assert_called_once()
