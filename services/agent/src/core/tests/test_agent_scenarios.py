import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.models import AgentRequest

from core.runtime.service import AgentService
from core.tests.mocks import MockLLMClient


@pytest.mark.asyncio
async def test_run_skill_flow(
    mock_agent_service: AgentService, mock_litellm: MockLLMClient, tmp_path: Path
) -> None:
    """
    Scenario: User asks agent to run a skill step.
    1. Mock Planner returns a skill-based plan.
    2. SkillExecutor runs the skill via LLM.
    3. Response includes plan step with executor='skill'.
    """
    # Mock Planner Response (skill-based plan)
    plan_json = {
        "description": "Answer using a skill.",
        "steps": [
            {
                "id": "step-1",
                "label": "Run skill",
                "executor": "skill",
                "action": "skill",
                "tool": "mock_skill",
                "args": {},
            },
        ],
    }

    skill_answer = "Mock skill answer."
    supervisor_ok = json.dumps({"decision": "ok", "reason": "Step executed successfully"})
    plan_supervisor_ok = json.dumps({"decision": "ok", "issues": [], "suggestions": []})

    responses: list[str | dict[str, Any]] = [
        json.dumps(plan_json),  # 1. Planner
        plan_supervisor_ok,  # 2. PlanSupervisor LLM review
        skill_answer,  # 3. SkillExecutor LLM call (stream_chat)
        supervisor_ok,  # 4. StepSupervisor review for skill step
    ]

    mock_litellm.responses = responses
    mock_litellm._response_index = 0

    request = AgentRequest(prompt="Answer my question.", conversation_id=str(uuid.uuid4()))

    mock_session = AsyncMock()
    mock_context = MagicMock()
    mock_context.id = uuid.uuid4()
    mock_context.default_cwd = str(tmp_path)
    mock_context.pinned_files = []

    mock_conversation = MagicMock()
    mock_conversation.context_id = mock_context.id
    mock_conversation.current_cwd = str(tmp_path)
    mock_conversation.conversation_metadata = {}

    async def side_effect_get(model: Any, id: Any) -> Any:
        if str(model.__name__) == "Conversation":
            return mock_conversation
        if str(model.__name__) == "Context":
            return mock_context
        return None

    mock_session.get.side_effect = side_effect_get

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    response = await mock_agent_service.handle_request(request, session=mock_session)

    # Verify skill step was executed
    skill_steps = [
        s for s in response.steps if s.get("type") == "plan_step" and s.get("executor") == "skill"
    ]
    assert len(skill_steps) > 0
    assert skill_steps[0]["tool"] == "mock_skill"
