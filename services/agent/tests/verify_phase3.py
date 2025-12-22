import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from core.core.service import AgentService
from core.db.engine import get_db
from shared.models import AgentRequest, Plan, PlanStep, RoutingDecision


async def main():
    print("Starting Phase 3 verification...")

    # 1. Setup Mocks
    settings = MagicMock()
    settings.litellm_model = "mock-model"
    settings.tool_result_max_chars = 1000

    litellm = AsyncMock()
    # Mock return for the skill execution (2nd call usually, or 1st if we bypass planner)
    # We expect `handle_request` -> `executor` -> `litellm.generate(rendered_prompt)`
    litellm.generate.return_value = "Hello, Tester!"

    memory = AsyncMock()
    tool_registry = MagicMock()
    tool_registry.tools.return_value = []
    tool_registry.get.return_value = None  # Ensure it falls back to skill

    service = AgentService(settings, litellm, memory, tool_registry)

    # 2. Verify Discovery
    print("Verifying Skill Discovery...")
    tools = service._describe_tools()
    skill_names = [t["name"] for t in tools]
    if "test_skill" not in skill_names:
        print(f"FAILED: test_skill not found in discovered tools: {skill_names}")
        # Identify why? maybe scanning path issue?
    else:
        print("Success: test_skill discovered.")

    # 3. Verify Execution (via Injected Plan)
    plan = Plan(
        description="Test Plan",
        steps=[
            PlanStep(
                id="step-1",
                label="Run Skill",
                executor="agent",  # Executor must be agent to use tools/skills
                action="tool",
                tool="test_skill",
                args={"target": "Tester"},
            ),
            PlanStep(  # Final step required by logic usually
                id="step-2", label="Answer", executor="litellm", action="completion"
            ),
        ],
    )

    conv_id = str(uuid.uuid4())
    req = AgentRequest(
        prompt="Run test skill",
        conversation_id=conv_id,
        messages=[],
        metadata={"routing_decision": RoutingDecision.AGENTIC, "plan": plan.model_dump()},
    )

    async for session in get_db():
        try:
            print("Verifying Skill Execution...")
            resp = await service.handle_request(req, session=session)
            print(f"Response: {resp.response}")

            # 4. Check Calls
            # We expect litellm.generate to have been called with the rendered prompt
            # The rendered prompt should be: "You are a greeter.\nSay 'Hello, Tester!' in a friendly way."

            # Gather all calls to generate
            calls = litellm.generate.call_args_list
            found_prompt = False
            for call in calls:
                # call.args[0] is list of messages
                messages = call.args[0]
                if messages and "You are a greeter" in messages[0].content:
                    found_prompt = True
                    break

            if found_prompt:
                print("Success: Litellm called with rendered skill prompt.")
            else:
                print("FAILED: Litellm NOT called with rendered skill prompt.")
                print(f"Calls: {calls}")

        except Exception as e:
            print(f"FAILED with Error: {e}")
            import traceback

            traceback.print_exc()
            raise
        finally:
            await session.rollback()
        break


if __name__ == "__main__":
    if not os.getenv("POSTGRES_URL"):
        os.environ["POSTGRES_URL"] = (
            "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_db"
        )
    asyncio.run(main())
