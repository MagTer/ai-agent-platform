import json
import uuid

import pytest
from shared.models import AgentRequest
from core.tests.mocks import MockLLMClient
from sqlalchemy.orm import Session


@pytest.mark.asyncio
async def test_run_tool_flow(mock_agent_service, mock_litellm, tmp_path):
    """
    Scenario: User asks to read a file.
    1. Mock Planner returns a plan to use 'read_file'.
    2. Agent executes 'read_file'.
    3. Mock Responder returns the answer.
    """
    # Setup: Create a real file to read
    test_file = tmp_path / "hello.txt"
    test_file.write_text("Hello World!")
    
    # Needs absolute path for tool
    abs_path = str(test_file.resolve())

    # Mock Planner Response (JSON Plan)
    plan_json = {
        "description": "Read the file and answer.",
        "steps": [
            {
                "id": "step-1",
                "label": "Read File",
                "executor": "agent",
                "action": "tool",
                "tool": "read_file",
                "args": {"path": abs_path}
            },
            {
                "id": "step-2",
                "label": "Answer",
                "executor": "litellm",
                "action": "completion",
                "args": {"model": "mock-model"}
            }
        ]
    }

    # Mock Responder Response (Final Answer)
    final_answer = f"The file contains: Hello World!"

    # Queue responses: 
    # 1. Planner Agent call -> returns plan_json
    # 2. Plan Supervisor review -> returns approval (implicitly handled if not mocked, but let's assume supervisor uses same LLM?)
    #    Actually supervisor prompts LLM "Review this plan...". Since we share one MockLLM, we need to queue logic carefully or make MockLLM smarter.
    #    For simplicity, let's just queue responses in order.
    #    Sequence:
    #    1. Planner -> Plan
    #    2. Supervisor (Review Plan) -> "looks good" (text)
    #    3. Step Supervisor (Review Step 1 result) -> "continue" (text) - wait, this might be loop
    #    4. Responder -> Final Answer

    # To make this robust, MockLLM in real world usually checks prompt content.
    # For now, let's queue enough "clean" responses.
    
    responses = [
        json.dumps(plan_json),          # 1. Planner
        final_answer,                   # 2. Step 2 Execution (Completion)
    ]
    
    # Override the mock_litellm responses
    mock_litellm.responses = responses
    # Reset index
    mock_litellm._response_index = 0

    # Execute
    request = AgentRequest(
        prompt=f"Read {abs_path}",
        conversation_id=str(uuid.uuid4())
    )

    # We need a db session. Conftest usually provides one, but we didn't add it.
    # Let's mock the session or use an in-memory sqlite if possible?
    # Our `AgentService.handle_request` requires `session: AsyncSession`.
    # Let's create a dummy AsyncMock for the session since we don't test DB persistence here strictly,
    # OR we use a real in-memory SQLite if `core.db.engine` allows.
    # Given the constraints, let's use `unittest.mock.AsyncMock` for session.
    
    from unittest.mock import AsyncMock, MagicMock
    mock_session = AsyncMock()
    # We need to handle `await session.get(Conversation, ...)` -> return None (trigger creation)
    # `session.execute(...)` -> result.scalar_one_or_none()
    
    # This is getting complex to mock fully without a real DB fixture.
    # Strategy: Mock `session.get` to return a dummy Conversation, `session.execute` to return dummy Context.
    
    # Mock Context retrieval
    mock_context = MagicMock()
    mock_context.id = uuid.uuid4()
    mock_context.default_cwd = str(tmp_path)
    mock_context.pinned_files = []
    
    mock_conversation = MagicMock()
    mock_conversation.context_id = mock_context.id
    mock_conversation.current_cwd = str(tmp_path)
    
    # session.get(Conversation, ...) -> None first time? Or let's imply it exists.
    # session.get(Context, ...) -> mock_context
    
    async def side_effect_get(model, id):
        if str(model.__name__) == "Conversation":
            return mock_conversation
        if str(model.__name__) == "Context":
            return mock_context
        return None
        
    mock_session.get.side_effect = side_effect_get
    
    # session.execute() for Session/History lookup
    # This is tricky. 
    # Let's simplify: Mock the `AgentService._memory` and bypass DB logic if possible?
    # No, `handle_request` is monolithic and hits DB.
    # We really should have an in-memory SQLITE fixture.
    # But for now, let's try to mock the specific calls `handle_request` makes.
    # Or better: Add a `db_session` fixture that uses `sqlite+aiosqlite:///:memory:` (requires `aiosqlite` dep).
    # Checking pyproject.toml -> `asyncpg` is used. `aiosqlite` not listed.
    # We can't use sqlite easily.
    
    # Fallback: Mock `session.execute` to return object with `.scalars().all()` returning empty list (no history).
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None # For session lookup -> triggers new session
    mock_result.scalars.return_value.all.return_value = [] # No history
    
    mock_session.execute.return_value = mock_result
    
    # Running the service
    response = await mock_agent_service.handle_request(request, session=mock_session)
    
    # Verification
    assert "Hello World!" in response.response
    
    # Verify tool call was made
    # For planned steps, the type is 'plan_step' and action is 'tool'
    tool_steps = [s for s in response.steps if s.get("type") == "plan_step" and s.get("action") == "tool"]
    assert len(tool_steps) > 0
    assert tool_steps[0]["tool"] == "read_file"
    # The result output might be nested or direct string depending on executor
    # StepResult.result for tool is {"name": ..., "status": ..., "output": ...}
    assert "Hello World!" in tool_steps[0]["result"]["output"]
