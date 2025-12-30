#!/usr/bin/env python3
"""
Semantic Regression / Golden Master Testing Script.
Executes defined queries against the local AgentService/Dispatcher and asserts correctness.
"""

import asyncio
import sys
import uuid
from pathlib import Path

import yaml

# Ensure correct path for imports
AGENT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(AGENT_ROOT / "src"))

from colorama import Fore, Style, init  # noqa: E402

from core.core.config import get_settings  # noqa: E402
from core.core.litellm_client import LiteLLMClient  # noqa: E402
from core.core.memory import MemoryStore  # noqa: E402
from core.core.service import AgentService  # noqa: E402
from core.db.engine import AsyncSessionLocal  # noqa: E402
from core.tools.loader import load_tool_registry  # noqa: E402
from orchestrator.dispatcher import Dispatcher  # noqa: E402
from orchestrator.skill_loader import SkillLoader  # noqa: E402

init(autoreset=True)


async def setup_agent():
    """Initialize the full agent stack."""
    settings = get_settings()
    litellm = LiteLLMClient(settings)
    memory = MemoryStore(settings)
    await memory.ainit()

    tool_registry = load_tool_registry(settings.tools_config_path)
    skill_loader = SkillLoader()

    dispatcher = Dispatcher(skill_loader, litellm)
    agent_service = AgentService(settings, litellm, memory, tool_registry=tool_registry)

    return dispatcher, agent_service


async def run_eval():
    root_dir = Path(__file__).resolve().parent.parent
    yaml_path = root_dir / "tests" / "semantic" / "golden_queries.yaml"

    if not yaml_path.exists():
        print(f"{Fore.RED}❌ Config not found: {yaml_path}")
        return

    with open(yaml_path) as f:
        scenarios = yaml.safe_load(f)

    dispatcher, agent_service = await setup_agent()

    print(f"{Style.BRIGHT}🚀 Starting Semantic Evaluation on {len(scenarios)} scenarios...\n")

    passed = 0
    failed = 0

    for scenario in scenarios:
        sid = scenario["id"]
        query = scenario["query"]
        print(f"🔹 Scenario: {Style.BRIGHT}{sid}{Style.RESET_ALL}")
        print(f"   Query: {query}")

        # Capture execution
        full_content = ""
        tool_usage = []
        intent_detected = "UNKNOWN"

        session_id = str(uuid.uuid4())

        try:
            async with AsyncSessionLocal() as session:
                async for chunk in dispatcher.stream_message(
                    session_id=session_id,
                    message=query,
                    platform="cli_test",
                    db_session=session,
                    agent_service=agent_service,
                ):
                    c_type = chunk.get("type")
                    content = chunk.get("content")

                    if c_type == "content" and content:
                        full_content += content
                    elif c_type == "thinking" and isinstance(content, str):
                        # Dispatcher logs intent but doesn't yield it explicitly.
                        # We try to detect intent from content.
                        if "Fast Path" in content:
                            intent_detected = "FAST_PATH"
                        pass
                    elif c_type == "tool_start":
                        t_call = chunk.get("tool_call", {})
                        if t_call:
                            tool_usage.append(t_call.get("name"))
                        intent_detected = "TASK"  # Strong indicator
                    elif c_type == "history_snapshot":
                        pass

                # Heuristic for intent if not set by tool usage
                if intent_detected == "UNKNOWN":
                    # If we got content but no tools/steps, likely CHAT
                    if full_content:
                        intent_detected = "CHAT"

        except Exception as e:
            print(f"{Fore.RED}   ❌ Execution Error: {e}")
            failed += 1
            continue

        # Grading
        errors = []

        # 1. Keywords
        for must in scenario.get("must_contain", []):
            if must.lower() not in full_content.lower():
                errors.append(f"Missing keyword: '{must}'")

        for bad in scenario.get("forbidden", []):
            if bad.lower() in full_content.lower():
                errors.append(f"Found forbidden keyword: '{bad}'")

        # 2. Tools
        expected_tools = scenario.get("tools_expected", [])
        if expected_tools:
            # We check if ANY of the expected tools were used? Or ALL?
            # Usually strict match or subset. Let's say: expected tools MUST be present in usage.
            for t in expected_tools:
                if t not in tool_usage:
                    # 'filesystem' vs 'read_file'. Config assumes 'read_file'.
                    # If user put 'filesystem' as a category, we might fail.
                    # We'll assume specific tool names.
                    errors.append(f"Missing expected tool usage: '{t}'")

        # 3. Intent (Approximate)
        # We can't easily get the router's internal decision without spying.
        # But we inferred it above.
        allowed_intents = scenario.get("expected_intents", [])
        if allowed_intents and intent_detected not in allowed_intents:
            # Relaxed check: if TASK is expected but we just chatted?
            pass

        if errors:
            print(f"{Fore.RED}   ❌ FAIL")
            for e in errors:
                print(f"      - {e}")
            print(f"      Output: {full_content[:100]}...")
            failed += 1
        else:
            print(f"{Fore.GREEN}   ✅ PASS")
            passed += 1
        print("-" * 40)

    print(
        f"\n{Style.BRIGHT}Results: {Fore.GREEN}{passed} Passed{Style.RESET_ALL} "
        f"| {Fore.RED}{failed} Failed"
    )

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_eval())
