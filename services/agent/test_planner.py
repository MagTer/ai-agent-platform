import json
from unittest.mock import AsyncMock

import pytest

from core.agents.planner import PlannerAgent
from core.core.litellm_client import LiteLLMClient
from shared.models import AgentRequest


@pytest.fixture
def mock_litellm():
    return AsyncMock(spec=LiteLLMClient)


@pytest.fixture
def planner(mock_litellm):
    return PlannerAgent(litellm=mock_litellm)


@pytest.fixture
def base_request():
    return AgentRequest(prompt="Build a website", conversation_id="123")


@pytest.mark.asyncio
async def test_generate_clean_json(planner, mock_litellm, base_request):
    """Test handling of clean JSON output."""
    clean_json = json.dumps(
        {
            "description": "Plan",
            "steps": [
                {"id": "1", "label": "s1", "executor": "agent", "action": "memory", "args": {}}
            ],
        }
    )
    mock_litellm.plan.return_value = clean_json

    plan = await planner.generate(base_request, history=[], tool_descriptions=[])

    assert plan.description == "Plan"
    assert len(plan.steps) == 1
    assert mock_litellm.plan.call_count == 1


@pytest.mark.asyncio
async def test_generate_markdown_block(planner, mock_litellm, base_request):
    """Test handling of JSON wrapped in markdown code blocks."""
    json_text = json.dumps(
        {
            "description": "Plan",
            "steps": [
                {"id": "1", "label": "s1", "executor": "agent", "action": "memory", "args": {}}
            ],
        }
    )
    markdown_text = f"Here is the plan:\n```json\n{json_text}\n```"
    mock_litellm.plan.return_value = markdown_text

    plan = await planner.generate(base_request, history=[], tool_descriptions=[])

    assert plan.description == "Plan"
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_generate_retry_on_invalid_json(planner, mock_litellm, base_request):
    """Test that the agent retries when receiving invalid JSON."""
    invalid_json = "This is not JSON"
    valid_json = json.dumps({"description": "Fixed Plan", "steps": []})

    # First call returns invalid, second returns valid
    mock_litellm.plan.side_effect = [invalid_json, valid_json]

    plan = await planner.generate(base_request, history=[], tool_descriptions=[])

    assert plan.description == "Fixed Plan"
    assert mock_litellm.plan.call_count == 2

    # Verify the second call included error feedback (inspect arguments)
    args, kwargs = mock_litellm.plan.call_args_list[1]
    messages = kwargs["messages"]
    # Expect: System, User, Assistant(Invalid), User(Fix it)
    assert len(messages) >= 4
    assert messages[-1].role == "user"
    assert "Invalid JSON" in messages[-1].content or "fix" in messages[-1].content.lower()


@pytest.mark.asyncio
async def test_generate_fail_after_retries(planner, mock_litellm, base_request):
    """Test fallback after exhaustion of retries."""
    mock_litellm.plan.return_value = "Still not JSON"

    # Depending on implementation, it might retry 2 or 3 times total
    plan = await planner.generate(base_request, history=[], tool_descriptions=[])

    # Defaults to empty/failed plan
    assert "Unable to parse" in plan.description or "failed" in plan.description
    assert mock_litellm.plan.call_count >= 1
