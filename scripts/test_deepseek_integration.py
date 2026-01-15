#!/usr/bin/env python3
"""
Integration tests for Deepseek hybrid model strategy.

Tests verify:
1. All tools have parameters attribute for deepseek compatibility
2. Model assignments are correct (completion-fast vs agentchat)
3. Tool schemas are properly formatted

Run: python scripts/test_deepseek_integration.py
"""

import sys
import tempfile
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "services" / "agent" / "src"))

# ruff: noqa: E402
from core.tools.azure_devops import AzureDevOpsTool
from core.tools.calculator import CalculatorTool
from core.tools.clock import ClockTool
from core.tools.filesystem import EditFileTool, ListDirectoryTool, ReadFileTool
from core.tools.github import GitHubTool
from core.tools.qa import RunPytestTool
from core.tools.search_code import SearchCodeBaseTool
from core.tools.test_runner import TestRunnerTool
from core.tools.tibp_wiki_search import TibpWikiSearchTool
from core.tools.web_fetch import WebFetchTool
from core.tools.web_search import WebSearchTool


class Colors:
    """ANSI color codes for terminal output."""

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"


def print_test_header(test_name: str) -> None:
    """Print formatted test header."""
    print(f"\n{Colors.BLUE}{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"{'=' * 70}{Colors.RESET}")


def print_success(message: str) -> None:
    """Print success message."""
    print(f"{Colors.GREEN}✓ {message}{Colors.RESET}")


def print_error(message: str) -> None:
    """Print error message."""
    print(f"{Colors.RED}✗ {message}{Colors.RESET}")


def print_warning(message: str) -> None:
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠ {message}{Colors.RESET}")


def test_tool_schemas() -> bool:
    """
    Test 1: Verify all tools have parameters attribute.

    This is CRITICAL - without parameters, deepseek cannot understand tool signatures.
    """
    print_test_header("Tool Schema Validation")

    # Tools that should have parameters (take arguments)
    tmpdir = tempfile.gettempdir()
    tools_with_params = [
        ("AzureDevOpsTool", AzureDevOpsTool(org_url="https://dev.azure.com/test", pat="fake")),
        ("CalculatorTool", CalculatorTool()),
        ("ListDirectoryTool", ListDirectoryTool(base_path=tmpdir)),
        ("ReadFileTool", ReadFileTool(base_path=tmpdir)),
        ("EditFileTool", EditFileTool(base_path=tmpdir)),
        ("GitHubTool", GitHubTool(token=None)),
        ("RunPytestTool", RunPytestTool(base_path=tmpdir)),
        ("SearchCodeBaseTool", SearchCodeBaseTool()),
        ("TestRunnerTool", TestRunnerTool()),
        ("TibpWikiSearchTool", TibpWikiSearchTool()),
        ("WebFetchTool", WebFetchTool(base_url="http://test")),
        ("WebSearchTool", WebSearchTool(base_url="http://test")),
    ]

    # Tools that don't need parameters (no arguments)
    tools_without_params = [
        ("ClockTool", ClockTool()),
    ]

    all_passed = True

    # Test tools that should have parameters
    for name, tool in tools_with_params:
        if not hasattr(tool, "parameters"):
            print_error(f"{name} missing 'parameters' attribute")
            all_passed = False
        elif not isinstance(tool.parameters, dict):
            print_error(f"{name} 'parameters' is not a dict")
            all_passed = False
        elif "type" not in tool.parameters:
            print_error(f"{name} 'parameters' missing 'type' field")
            all_passed = False
        elif "properties" not in tool.parameters:
            print_error(f"{name} 'parameters' missing 'properties' field")
            all_passed = False
        else:
            print_success(f"{name} has valid parameters schema")

    # Verify tools without params don't accidentally have them
    for name, tool in tools_without_params:
        if hasattr(tool, "parameters"):
            print_warning(f"{name} has parameters but doesn't need them (takes no args)")
        else:
            print_success(f"{name} correctly has no parameters (takes no args)")

    return all_passed


def test_skill_model_assignments() -> bool:
    """
    Test 2: Verify skill model assignments are correct.

    Checks YAML frontmatter for model assignments.
    """
    print_test_header("Skill Model Assignments")

    skills_dir = project_root / "skills"

    # Expected model assignments based on implementation plan
    expected_assignments = {
        "work/backlog_manager.md": "skillsrunner-complex",
        "work/requirements_writer.md": "skillsrunner",
        "work/requirements_drafter.md": "skillsrunner-complex",
        "general/researcher.md": "skillsrunner-complex",
        "general/deep_researcher.md": "skillsrunner-complex",
        "development/software_engineer.md": "skillsrunner-complex",
    }

    all_passed = True

    for skill_path, expected_model in expected_assignments.items():
        full_path = skills_dir / skill_path

        if not full_path.exists():
            print_error(f"{skill_path} does not exist")
            all_passed = False
            continue

        # Read YAML frontmatter
        content = full_path.read_text(encoding="utf-8")

        # Extract model value from YAML frontmatter
        model_line = None
        for line in content.split("\n"):
            if line.startswith("model:"):
                model_line = line.split(":", 1)[1].strip()
                break

        if model_line is None:
            print_error(f"{skill_path} missing 'model:' in frontmatter")
            all_passed = False
        elif model_line != expected_model:
            print_error(f"{skill_path} has model={model_line}, expected {expected_model}")
            all_passed = False
        else:
            print_success(f"{skill_path} correctly uses model={expected_model}")

    return all_passed


def test_max_turns_assignments() -> bool:
    """
    Test 3: Verify max_turns are set according to complexity.

    Complex skills (researcher) should have higher turns.
    Simple skills (backlog_manager) should have lower turns.
    """
    print_test_header("Max Turns Assignments")

    skills_dir = project_root / "skills"

    # Expected max_turns based on implementation plan
    expected_turns = {
        "work/backlog_manager.md": 3,
        "work/requirements_writer.md": 3,
        "work/requirements_drafter.md": 5,
        "general/researcher.md": 7,
        "general/deep_researcher.md": 10,
    }

    all_passed = True

    for skill_path, expected_max_turns in expected_turns.items():
        full_path = skills_dir / skill_path

        if not full_path.exists():
            print_error(f"{skill_path} does not exist")
            all_passed = False
            continue

        # Read YAML frontmatter
        content = full_path.read_text(encoding="utf-8")

        # Extract max_turns value
        max_turns_line = None
        for line in content.split("\n"):
            if line.startswith("max_turns:"):
                max_turns_str = line.split(":", 1)[1].strip()
                max_turns_line = int(max_turns_str)
                break

        if max_turns_line is None:
            print_error(f"{skill_path} missing 'max_turns:' in frontmatter")
            all_passed = False
        elif max_turns_line != expected_max_turns:
            print_error(
                f"{skill_path} has max_turns={max_turns_line}, expected {expected_max_turns}"
            )
            all_passed = False
        else:
            print_success(f"{skill_path} correctly has max_turns={expected_max_turns}")

    return all_passed


def test_litellm_config() -> bool:
    """
    Test 4: Verify litellm config has composer and skillsrunner models.

    Checks that composer, skillsrunner, and skillsrunner-complex aliases are configured.
    """
    print_test_header("LiteLLM Configuration")

    config_path = project_root / "services" / "litellm" / "config.yaml"

    if not config_path.exists():
        print_error(f"Config file not found: {config_path}")
        return False

    content = config_path.read_text(encoding="utf-8")

    all_passed = True

    # Check for composer model
    if "model_name: composer" not in content:
        print_error("composer model alias not found in config")
        all_passed = False
    else:
        print_success("composer model alias configured")

    # Check for skillsrunner-complex model
    if "model_name: skillsrunner-complex" not in content:
        print_error("skillsrunner-complex model alias not found in config")
        all_passed = False
    else:
        print_success("skillsrunner-complex model alias configured")

    # Check for skillsrunner model
    if "model_name: skillsrunner" not in content:
        print_error("skillsrunner model alias not found in config")
        all_passed = False
    else:
        print_success("skillsrunner model alias configured")

    # Check for Llama 3.3 70B model (composer and skillsrunner)
    if content.count("meta-llama/llama-3.3-70b-instruct") < 2:
        print_error("Llama 3.3 70B not configured for composer and skillsrunner")
        all_passed = False
    else:
        print_success("composer and skillsrunner use Llama 3.3 70B Instruct")

    # Verify deepseek models are still configured for planner/supervisor
    if "model_name: planner" not in content:
        print_error("planner model not found")
        all_passed = False
    elif "deepseek" not in content:
        print_warning("planner may not be using deepseek")
    else:
        print_success("planner model configured")

    return all_passed


def test_execution_protocol() -> bool:
    """
    Test 5: Verify skill_delegate has PROGRESSIVE RESEARCH protocol.

    Checks that execution protocol allows iterative tool calling.
    """
    print_test_header("Execution Protocol")

    skill_delegate_path = (
        project_root / "services" / "agent" / "src" / "core" / "tools" / "skill_delegate.py"
    )

    if not skill_delegate_path.exists():
        print_error(f"skill_delegate.py not found: {skill_delegate_path}")
        return False

    content = skill_delegate_path.read_text(encoding="utf-8")

    all_passed = True

    # Check for PROGRESSIVE RESEARCH (not strict ONE CALL)
    if "PROGRESSIVE RESEARCH" in content:
        print_success("Execution protocol allows progressive research")
    else:
        print_error("PROGRESSIVE RESEARCH not found in execution protocol")
        all_passed = False

    # Ensure strict ONE CALL protocol was removed
    if "ONE CALL PER QUERY: Each unique question = exactly ONE tool call" in content:
        print_error("Strict ONE CALL protocol still present (should be removed)")
        all_passed = False
    else:
        print_success("Strict ONE CALL protocol removed")

    # Check for budget constraints (should still be present)
    if "Maximum turns:" in content and "Maximum calls per tool type" in content:
        print_success("Budget constraints present (prevents infinite loops)")
    else:
        print_warning("Budget constraints may be missing")

    return all_passed


def main() -> None:
    """Run all integration tests."""
    print(f"{Colors.BLUE}\n{'=' * 70}")
    print("Deepseek Integration Test Suite")
    print(f"{'=' * 70}{Colors.RESET}\n")

    # Run all tests
    test_results = {
        "Tool Schema Validation": test_tool_schemas(),
        "Skill Model Assignments": test_skill_model_assignments(),
        "Max Turns Assignments": test_max_turns_assignments(),
        "LiteLLM Configuration": test_litellm_config(),
        "Execution Protocol": test_execution_protocol(),
    }

    # Print summary
    print(f"\n{Colors.BLUE}{'=' * 70}")
    print("TEST SUMMARY")
    print(f"{'=' * 70}{Colors.RESET}\n")

    passed_count = sum(1 for passed in test_results.values() if passed)
    total_count = len(test_results)

    for test_name, passed in test_results.items():
        if passed:
            status = f"{Colors.GREEN}PASSED{Colors.RESET}"
        else:
            status = f"{Colors.RED}FAILED{Colors.RESET}"
        print(f"  {test_name}: {status}")

    print(f"\n{Colors.BLUE}{'=' * 70}{Colors.RESET}")
    if passed_count == total_count:
        print(f"{Colors.GREEN}✓ All tests passed ({passed_count}/{total_count}){Colors.RESET}")
        print(f"{Colors.GREEN}\nDeepseek integration is ready for deployment!{Colors.RESET}\n")
        sys.exit(0)
    else:
        print(
            f"{Colors.RED}✗ Some tests failed ({passed_count}/{total_count} passed){Colors.RESET}"
        )
        print(f"{Colors.RED}\nPlease fix failing tests before deployment.{Colors.RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
