---
focus: quality
generated: 2026-03-28
---

# Testing Patterns

**Analysis Date:** 2026-03-28

## Test Framework

**Runner:** pytest 9.0.0
**Async mode:** `asyncio_mode = "auto"` — all `async def test_*` functions run automatically without needing `@pytest.mark.asyncio`. Explicit `@pytest.mark.asyncio` decorators are still added in some older files (516 occurrences) but the auto mode makes them redundant for new tests.
**Assertion library:** pytest built-in
**Async support:** pytest-asyncio 1.3.0
**Selective execution:** pytest-testmon 2.2.0 (CI only runs tests affected by changed files)
**Config:** `services/agent/pyproject.toml` under `[tool.pytest.ini_options]`

**Run Commands:**
```bash
# All checks including tests (preferred - matches CI exactly)
./stack check

# Tests only via stack CLI
./stack test

# Direct pytest - all tests
cd services/agent && python -m pytest

# Specific test file
pytest services/agent/src/core/tests/test_service.py -v

# Single test
pytest services/agent/src/core/tests/test_service.py::TestClassName::test_method -v

# With testmon (affected tests only - matches CI)
cd services/agent && python -m pytest -x --testmon

# With coverage
pytest services/agent/src/core/tests/ --cov=services/agent/src --cov-report=html

# Verbose with log output
pytest services/agent/src/core/tests/ -v --log-cli-level=INFO
```

**Note:** No `--timeout` flag is available in the pytest config. Use `--log-cli-level=INFO` for debugging slow tests.

## Test File Organization

**Primary location:** `src/*/tests/` co-located near source packages.

**Test paths registered in pyproject.toml:**
```
services/agent/
├── src/
│   ├── core/
│   │   ├── tests/               # 50+ test files, ~1147 test functions
│   │   │   ├── mocks.py         # MockLLMClient, InMemoryAsyncSession
│   │   │   ├── test_agent_scenarios.py   # Integration scenario tests
│   │   │   ├── test_planner_agent.py
│   │   │   ├── test_executor_agent.py
│   │   │   ├── test_service.py
│   │   │   ├── test_skill_executor.py
│   │   │   ├── test_supervisors.py
│   │   │   └── ... (40+ more)
│   │   ├── auth/tests/          # Auth unit tests
│   │   ├── context/tests/       # Context service tests
│   │   ├── observability/tests/ # Span rotation, OTel, metrics, debug logger
│   │   └── tools/tests/         # Tool unit tests (homey, security)
│   ├── interfaces/http/tests/   # Admin portal, session auth
│   ├── modules/
│   │   ├── email/tests/
│   │   └── price_tracker/tests/
│   ├── shared/tests/            # Shared model tests
│   ├── stack/tests/             # Stack CLI tests
│   └── utils/tests/
├── tests/                       # Legacy + semantic + integration
│   ├── semantic/                # Black-box E2E tests (real LLM)
│   │   ├── golden_queries.yaml  # 29 golden queries across 6 categories
│   │   ├── test_end_to_end.py
│   │   ├── conftest.py
│   │   ├── llm_evaluator.py
│   │   └── stream_parser.py
│   └── integration/             # Real DB, mocked LLM (NOT in CI testpaths)
└── conftest.py                  # Root fixtures: mock_agent_service, mock_litellm, mock_settings
```

**Do NOT add new tests to `tests/` root dirs** (`tests/core`, `tests/integration`, `tests/interfaces`, `tests/unit`). These are pre-existing broken test dirs excluded from CI. New tests always go in `src/*/tests/` near source code.

## Test Pyramid

### Layer 1: Unit Tests (fast, mocked)

Located in `src/core/tests/`, `src/core/*/tests/`, `src/modules/*/tests/`, etc.

Use `MockLLMClient` and `InMemoryAsyncSession` from `services/agent/src/core/tests/mocks.py`. No real database, no real LLM, no network calls.

```python
from core.tests.mocks import MockLLMClient

@pytest.fixture
def planner(mock_litellm: MockLLMClient) -> PlannerAgent:
    return PlannerAgent(litellm=mock_litellm, model_name="test-planner")

async def test_generates_valid_plan(planner: PlannerAgent) -> None:
    mock_litellm.responses = [json.dumps({"steps": [...]})]
    result = await planner.plan(AgentRequest(prompt="test"))
    assert result.steps[0].executor == "skill"
```

### Layer 2: Integration Tests / Scenario Tests (real DB, mocked LLM)

Located primarily in `src/core/tests/test_agent_scenarios.py`.

Full request flows through `AgentService`. Use `MockLLMClient` with a queue of pre-programmed responses that simulate the multi-turn LLM interaction (planner, supervisor, executor, responder). Each scenario queues responses in call order.

```python
async def test_run_tool_flow(
    mock_agent_service: AgentService, mock_litellm: MockLLMClient, tmp_path: Path
) -> None:
    supervisor_ok = json.dumps({"decision": "ok", "reason": "Step executed successfully"})
    plan_supervisor_ok = json.dumps({"decision": "ok", "issues": [], "suggestions": []})

    mock_litellm.responses = [
        json.dumps(plan_json),  # 1. Planner
        plan_supervisor_ok,     # 2. PlanSupervisor LLM review
        supervisor_ok,          # 3. StepSupervisor review for step 1
        final_answer,           # 4. Step 2 execution
        supervisor_ok,          # 5. StepSupervisor review for step 2
    ]
    mock_litellm._response_index = 0
    # ... assert on result
```

Every new feature flow needs a scenario test in `src/core/tests/test_agent_scenarios.py`.

### Layer 3: Semantic Tests (slow, real LLM, black-box)

Located in `services/agent/tests/semantic/`.

Treat the running agent as a black box via HTTP. Require the full stack to be running (`docker-compose up`). Make real LLM calls (30-120 seconds each). NOT in CI test paths — run manually before production deploys.

```bash
# Run by category (fast, ~30s)
./stack test --semantic-category routing

# Full semantic regression
./stack test --semantic
```

**29 golden queries** in `tests/semantic/golden_queries.yaml` across 6 categories:

| Category | Count | Purpose |
|----------|-------|---------|
| routing | 5 | Intent classification and skill selection |
| skills | 11 | Specific skill execution (researcher, homey, backlog, etc.) |
| tools | 4 | Direct tool invocation |
| planning | 3 | Multi-step plan generation |
| regression | 3 | Previously broken scenarios |
| error | 3 | Graceful error handling |

Each query defines `must_contain`, `must_contain_pattern`, `forbidden`, and optionally `min_response_length`.

## Key Fixtures and Mocks

### `MockLLMClient` (`src/core/tests/mocks.py`)

Deterministic LLM mock. Pre-load responses as a list; each `generate()` or `stream_chat()` call consumes the next response.

```python
from core.tests.mocks import MockLLMClient

mock_llm = MockLLMClient(responses=[
    '{"steps": [...]}',    # consumed by first generate() call
    "Final answer here",   # consumed by second generate() call
])

# Access call history for assertions
assert len(mock_llm.call_history) == 2
assert mock_llm.call_history[0][0].role == "system"
```

Inherits from `LiteLLMClient` (actual prod class) so mypy validates the interface. Use the `_llm()` cast helper pattern for accessing custom attributes:

```python
def _llm(planner: PlannerAgent) -> MockLLMClient:
    return planner._litellm  # type: ignore[return-value]
```

### `InMemoryAsyncSession` (`src/core/tests/mocks.py`)

SQLAlchemy `AsyncSession` replacement. Stores objects in an in-memory dict. Supports `get()`, `add()`, `delete()`, `commit()`, `rollback()`, `execute()` (returns a `MagicMock` result).

```python
from core.tests.mocks import InMemoryAsyncSession

session = InMemoryAsyncSession()
session.add(my_model_instance)
await session.commit()
fetched = await session.get(MyModel, my_model_instance.id)
```

### Root conftest (`services/agent/conftest.py`)

Provides session-scoped fixtures used across all test paths:

- `mock_litellm` — `MockLLMClient()` with no pre-loaded responses
- `mock_settings` — `Settings` with mock LiteLLM config, no real secrets
- `mock_memory_store` — Real `MemoryStore` instantiated with mock settings
- `mock_agent_service` — Full `AgentService` with `ReadFileTool` registered

### Standard unittest.mock usage

```python
from unittest.mock import AsyncMock, MagicMock, patch

# Async method mock
mock_tool = MagicMock(spec=Tool)
mock_tool.run = AsyncMock(return_value="Tool output")

# Patch a module-level function
with patch("core.auth.credential_service.get_db") as mock_db:
    mock_db.return_value = InMemoryAsyncSession()
    ...

# Patch a class entirely
with patch("core.runtime.litellm_client.LiteLLMClient") as MockClient:
    MockClient.return_value.generate = AsyncMock(return_value="response")
    ...
```

## Test Structure Pattern

```python
"""Module docstring describing what is tested."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.models import AgentRequest

from core.agents.planner import PlannerAgent
from core.tests.mocks import MockLLMClient


# Helper cast for mock attributes (mypy compatible)
def _llm(agent: PlannerAgent) -> MockLLMClient:
    return agent._litellm  # type: ignore[return-value]


# Module-level fixtures (shared across test classes)
@pytest.fixture
def mock_litellm() -> MockLLMClient:
    """Create a mock LiteLLM client."""
    return MockLLMClient(responses=[])


@pytest.fixture
def planner(mock_litellm: MockLLMClient) -> PlannerAgent:
    """Create a PlannerAgent with mocked dependencies."""
    return PlannerAgent(litellm=mock_litellm, model_name="test-planner")


# Test classes group related scenarios
class TestPlannerAgent:
    """Tests for plan generation behavior."""

    async def test_generates_plan_for_simple_request(
        self, planner: PlannerAgent, mock_litellm: MockLLMClient
    ) -> None:
        """Normal requests should produce a valid plan."""
        mock_litellm.responses = ['{"steps": [...]}']
        result = await planner.plan(AgentRequest(prompt="What is 2+2?"))
        assert result is not None

    async def test_raises_on_malformed_llm_response(
        self, planner: PlannerAgent, mock_litellm: MockLLMClient
    ) -> None:
        mock_litellm.responses = ["not valid json"]
        with pytest.raises(ValueError):
            await planner.plan(AgentRequest(prompt="test"))
```

## Mocking Strategy

**Mock at the boundary.** Mock external I/O (LLM calls, DB, HTTP, filesystem) at the interface level, not deep in implementation details.

**What to mock:**
- `LiteLLMClient` — use `MockLLMClient`
- `AsyncSession` — use `InMemoryAsyncSession` or `MagicMock(spec=AsyncSession)`
- External HTTP — use `httpx.MockTransport` or `AsyncMock`
- Tool implementations when testing agents — `MagicMock(spec=Tool)` with `tool.run = AsyncMock(...)`

**What NOT to mock:**
- Pydantic models and data classes
- Pure functions with no I/O
- `SkillRegistry` when testing skill loading (use `tmp_path` with real `.md` files)

## CI Configuration

**Trigger:** Pull requests only. No push-to-main triggers (except `testmon-cache.yml`).

**Jobs in `ci.yml`:**

| Job | Tool | Command |
|-----|------|---------|
| Lint | Ruff + Black | `ruff check .` + `black --check .` |
| Typecheck | Mypy | `python -m mypy` |
| Tests | Pytest + testmon | `pytest -x --testmon` |
| Compose | Docker Compose | `docker compose config` |
| Container Scan | Trivy | Image scan for HIGH/CRITICAL CVEs |
| Dependency Audit | pip-audit | `pip-audit --strict` |

**Quality Gate job** requires all 6 jobs to succeed. This is the single required branch protection check.

**Testmon selective execution:** CI runs `pytest -x --testmon` which skips tests unaffected by changed files. This is incompatible with pytest-xdist (parallel) and branch coverage measurement.

**Testmon cache:** Stored in `services/agent/.testmondata` (SQLite). CI caches this between runs keyed on `poetry.lock` hash + commit SHA. After each run, WAL mode is checkpointed:
```python
import sqlite3
c = sqlite3.connect('.testmondata')
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.close()
```

**Testmon cache seeding:** `testmon-cache.yml` runs on every push to `main` to keep the cache warm. This ensures PR CI has a valid baseline to diff against.

**Do NOT include `.testmondata` in git.** It is a build artifact. Similarly exclude `.venv/`, `__pycache__/`, `.stack/dev-deployments.json`.

## Test Naming Conventions

**Pattern:** `test_<action>_<scenario>_<expected_result>`

```python
def test_parse_response_with_missing_field_raises_error(): ...
def test_execute_step_returns_retry_on_timeout(): ...
def test_valid_plan_passes_supervisor_review(): ...
def test_sanitize_user_input_removes_markdown_code_fences(): ...
```

**Class naming:** `Test<ComponentName>` or `Test<MethodName>`

```python
class TestPlanSupervisorAgent: ...
class TestSanitizeUserInput: ...
class TestStepExecutorAgentRun: ...
```

## Coverage

**Branch coverage enabled** in `[tool.coverage.run]` with `branch = true`.

**Source:** `services/agent/src`

**Excluded lines:**
- `pragma: no cover`
- `if __name__ == "__main__":`
- `if TYPE_CHECKING:`

**View coverage:**
```bash
pytest services/agent/src/ --cov=services/agent/src --cov-report=html
# Open services/agent/htmlcov/index.html
```

No minimum coverage percentage is enforced in CI, but test count is a strong proxy. Currently 1147+ test functions in `src/`.

## Integration Test Markers

Two markers defined (but not enforced as CI gates):

```python
@pytest.mark.integration  # Requires running docker-compose stack
async def test_real_db_operation(): ...

@pytest.mark.smoke  # Post-deploy functional check
async def test_health_endpoint_returns_200(): ...
```

Integration tests in `tests/integration/` are not in the CI testpaths and must be run manually against a live stack:
```bash
pytest services/agent/tests/integration/test_openrouter_models.py -v -s
```

## Security Test Patterns

Security-focused tests in `src/core/tests/`:

- `test_fetcher_ssrf.py` — SSRF protection in WebFetcher (redirect validation)
- `test_sanitization.py` — Input sanitization / prompt injection
- `test_header_auth.py` — Admin portal auth header validation
- `test_internal_api_auth.py` — Internal API key authentication
- `test_web_tools_security.py` — Web tool security boundaries

Forbidden patterns checked in semantic tests (`test_end_to_end.py`):
- Python tracebacks (`Traceback`, `File "*.py"`, `line \d+, in`)
- Raw exception type names (`SQLAlchemyError`, `KeyError`, `TypeError`)
- Meta-commentary patterns (`"Let me search"`, `"I will now"`)

## TDD Workflow

1. Write a failing test that defines expected behavior
2. Implement minimum code to make it pass
3. Refactor while keeping tests green
4. Run `./stack check` (ruff + black + mypy + pytest) before committing
5. `stack check` must pass before every push to a PR branch — including follow-up fix commits and merge conflict resolution commits

---

*Testing analysis: 2026-03-28*
