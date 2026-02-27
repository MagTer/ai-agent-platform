#!/usr/bin/env python3
"""
Semantic Regression / Golden Master Testing Script.
Executes defined queries against the running agent via HTTP and asserts correctness.

Usage:
    python run_semantic_eval.py                    # Run all tests
    python run_semantic_eval.py --category routing # Run only routing tests
    python run_semantic_eval.py --category skills  # Run only skill tests
    python run_semantic_eval.py --id skill_researcher_basic  # Run single test
    python run_semantic_eval.py --list             # List available tests
    python run_semantic_eval.py --url http://localhost:8001  # Custom agent URL

Requires:
    - Running agent stack (./stack dev up or ./stack up)
    - Valid user in database (default: test@example.com)
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
import yaml
from colorama import Fore, Style, init

init(autoreset=True)

# Configuration
AGENT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENT_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8001")
DEFAULT_USER_EMAIL = os.getenv("TEST_USER_EMAIL", "test@example.com")
REQUEST_TIMEOUT = 120.0  # 2 minutes for LLM responses


def load_scenarios(yaml_path: Path) -> list[dict]:
    """Load and validate scenarios from YAML."""
    if not yaml_path.exists():
        print(f"{Fore.RED}Config not found: {yaml_path}")
        sys.exit(1)

    with open(yaml_path) as f:
        scenarios = yaml.safe_load(f)

    return scenarios


def filter_scenarios(
    scenarios: list[dict],
    category: str | None = None,
    test_id: str | None = None,
) -> list[dict]:
    """Filter scenarios by category or ID."""
    if test_id:
        return [s for s in scenarios if s["id"] == test_id]
    if category:
        return [s for s in scenarios if s.get("category") == category]
    return scenarios


def list_scenarios(scenarios: list[dict]) -> None:
    """Print available test scenarios."""
    print(f"\n{Style.BRIGHT}Available Test Scenarios:{Style.RESET_ALL}\n")

    by_category: dict[str, list[dict]] = {}
    for s in scenarios:
        cat = s.get("category", "uncategorized")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(s)

    for cat, tests in sorted(by_category.items()):
        print(f"{Fore.CYAN}{cat.upper()}{Style.RESET_ALL} ({len(tests)} tests)")
        for t in tests:
            skill = t.get("skill", "")
            skill_str = f" [{skill}]" if skill else ""
            print(f"  - {t['id']}{skill_str}")
        print()


def grade_response(
    scenario: dict,
    full_content: str,
    tool_usage: list[str],
    skill_usage: list[str],
) -> list[str]:
    """Grade the response against scenario expectations. Returns list of errors."""
    errors = []

    # 1. Must contain (exact, case-insensitive)
    for must in scenario.get("must_contain", []):
        if must.lower() not in full_content.lower():
            errors.append(f"Missing keyword: '{must}'")

    # 2. Must contain pattern (regex)
    for pattern in scenario.get("must_contain_pattern", []):
        if not re.search(pattern, full_content, re.IGNORECASE):
            errors.append(f"Missing pattern: '{pattern}'")

    # 3. Forbidden keywords
    for bad in scenario.get("forbidden", []):
        if bad.lower() in full_content.lower():
            errors.append(f"Found forbidden: '{bad}'")

    # 4. Minimum response length
    min_length = scenario.get("min_response_length", 0)
    if min_length and len(full_content) < min_length:
        errors.append(f"Response too short: {len(full_content)} < {min_length}")

    # 5. Expected tools
    expected_tools = scenario.get("tools_expected", [])
    for t in expected_tools:
        if t not in tool_usage:
            errors.append(f"Missing tool: '{t}'")

    # 6. Expected skills
    expected_skills = scenario.get("skills_expected", [])
    if expected_skills:
        found_any = any(s in skill_usage for s in expected_skills)
        if not found_any:
            errors.append(f"Missing skill: expected one of {expected_skills}")

    return errors


async def check_agent_health(client: httpx.AsyncClient, base_url: str, user_email: str) -> bool:
    """Check if agent is healthy by testing the docs endpoint."""
    try:
        # First check if the server is up
        resp = await client.get(f"{base_url}/docs", timeout=10.0)
        if resp.status_code != 200:
            return False

        # Then verify auth works with a simple models list
        headers = {"X-OpenWebUI-User-Email": user_email}
        resp = await client.get(
            f"{base_url}/v1/models",
            headers=headers,
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


async def run_scenario_http(
    scenario: dict,
    client: httpx.AsyncClient,
    base_url: str,
    user_email: str,
) -> tuple[bool, list[str], str]:
    """Run a single scenario via HTTP. Returns (passed, errors, output_preview)."""
    query = scenario["query"]
    full_content = ""
    tool_usage: list[str] = []
    skill_usage: list[str] = []

    headers = {
        "Content-Type": "application/json",
        "X-OpenWebUI-User-Email": user_email,
    }

    payload = {
        "model": "agent",
        "messages": [{"role": "user", "content": query}],
        "stream": True,
    }

    try:
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                return False, [f"HTTP {response.status_code}: {error_text[:200]}"], ""

            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue

                data_str = line[6:]  # Remove "data: " prefix
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)

                    # Extract content from OpenAI-compatible format
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full_content += content

                    # Check for tool/skill info in metadata (if present)
                    metadata = chunk.get("metadata", {})
                    if metadata:
                        if "tool_name" in metadata:
                            tool_usage.append(metadata["tool_name"])
                        if "skill_name" in metadata:
                            skill_usage.append(metadata["skill_name"])

                except json.JSONDecodeError:
                    continue

    except httpx.TimeoutException:
        return False, ["Request timed out"], ""
    except httpx.ConnectError:
        return False, ["Connection failed - is the agent running?"], ""
    except Exception as e:
        return False, [f"Request error: {e}"], ""

    errors = grade_response(scenario, full_content, tool_usage, skill_usage)
    output_preview = full_content[:150] + "..." if len(full_content) > 150 else full_content

    return len(errors) == 0, errors, output_preview


async def run_eval(
    base_url: str,
    user_email: str,
    category: str | None = None,
    test_id: str | None = None,
    verbose: bool = False,
) -> None:
    """Run the semantic evaluation via HTTP."""
    yaml_path = AGENT_ROOT / "tests" / "semantic" / "golden_queries.yaml"
    scenarios = load_scenarios(yaml_path)
    filtered = filter_scenarios(scenarios, category=category, test_id=test_id)

    if not filtered:
        print(f"{Fore.YELLOW}No scenarios found matching criteria")
        return

    print(f"\n{Style.BRIGHT}Semantic Evaluation (HTTP Mode){Style.RESET_ALL}")
    print(f"  Agent URL: {base_url}")
    print(f"  User: {user_email}")
    category_str = f"  Category: {category}" if category else ""
    if category_str:
        print(category_str)
    print(f"  Tests: {len(filtered)} of {len(scenarios)}\n")

    async with httpx.AsyncClient() as client:
        # Health check
        print(f"{Fore.CYAN}Checking agent health...{Style.RESET_ALL}")
        if not await check_agent_health(client, base_url, user_email):
            print(f"{Fore.RED}Agent not healthy at {base_url}")
            print(f"Start with: ./stack dev up{Style.RESET_ALL}")
            sys.exit(1)
        print(f"{Fore.GREEN}Agent is healthy{Style.RESET_ALL}\n")

        passed = 0
        failed = 0

        for scenario in filtered:
            sid = scenario["id"]
            query = scenario["query"]
            cat = scenario.get("category", "")
            skill = scenario.get("skill", "")

            cat_str = f"[{cat}]" if cat else ""
            skill_str = f" ({skill})" if skill else ""

            header = f"{Fore.CYAN}{cat_str}{Style.RESET_ALL} {Style.BRIGHT}{sid}"
            print(f"{header}{Style.RESET_ALL}{skill_str}")

            if verbose:
                print(f"   Query: {query[:60]}...")

            success, errors, output_preview = await run_scenario_http(
                scenario, client, base_url, user_email
            )

            if success:
                print(f"   {Fore.GREEN}PASS{Style.RESET_ALL}")
                passed += 1
            else:
                print(f"   {Fore.RED}FAIL{Style.RESET_ALL}")
                for e in errors:
                    print(f"      - {e}")
                if verbose and output_preview:
                    print(f"      Output: {output_preview}")
                failed += 1

            print()

    # Summary
    print(f"{Style.BRIGHT}{'='*50}{Style.RESET_ALL}")
    print(f"{Style.BRIGHT}Results:{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}Passed: {passed}{Style.RESET_ALL}")
    print(f"  {Fore.RED}Failed: {failed}{Style.RESET_ALL}")
    print(f"  Total:  {len(filtered)}")

    if failed > 0:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run semantic regression tests against the agent (HTTP mode)"
    )
    parser.add_argument(
        "--category",
        "-c",
        choices=["routing", "skills", "tools", "planning", "error", "regression"],
        help="Run only tests in this category",
    )
    parser.add_argument(
        "--id",
        help="Run a specific test by ID",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List available tests without running them",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show more details during execution",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_AGENT_URL,
        help=f"Agent base URL (default: {DEFAULT_AGENT_URL})",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_USER_EMAIL,
        help=f"Test user email (default: {DEFAULT_USER_EMAIL})",
    )

    args = parser.parse_args()

    if args.list:
        yaml_path = AGENT_ROOT / "tests" / "semantic" / "golden_queries.yaml"
        scenarios = load_scenarios(yaml_path)
        list_scenarios(scenarios)
        return

    asyncio.run(
        run_eval(
            base_url=args.url,
            user_email=args.user,
            category=args.category,
            test_id=args.id,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
