# Refactor Phase 6: Testing Infrastructure & System Documentation

## 1. Goal Description
The objective is to establish a rigorous testing culture and update the system documentation to reflect the current "Modular Monolith" architecture. We will strictly enforce testing via a "Safety Net" of mocks to allow scenario testing without external API calls, and rewrite `GEMINI.md` to be the single source of truth for all contributors.

## 2. Testing Infrastructure (The "Safety Net")

### 2.1. Mock Implementation
**File:** `services/agent/src/core/tests/mocks.py`
We need a `MockLLMClient` that implements the `LiteLLMClient` interface but returns deterministic responses.

- **Class `MockLLMClient`**:
    - `__init__(self, responses: dict[str, str | dict] | list[str])`
    - `generate(self, messages, ...)`: Returns next response from queue or matched response.
    - `plan(self, messages, ...)`: Returns a fixed plan JSON.

### 2.2. Test Fixtures
**File:** `services/agent/conftest.py`
- `mock_litellm`: Returns a `MockLLMClient`.
- `mock_agent_service`: Creates an `AgentService` injected with `mock_litellm` and an in-memory `MemoryStore` (or mocked one).

### 2.3. Scenario Tests
**File:** `services/agent/src/core/tests/test_agent_scenarios.py`
- `test_run_tool_flow`:
    1.  **Setup**: Mock LLM programmed to return a Plan that calls `read_file(test_file)`.
    2.  **Execute**: `service.handle_request("Read test_file")`.
    3.  **Verify**:
        - Response contains file content.
        - Tool was actually called (via spy or result inspection).
        - No network calls were made.

## 3. System Documentation (`GEMINI.md`) Update
The `GEMINI.md` file will be rewritten to serve as the "Constitution" for the project.

### Structure
1.  **Identity & Role**: Senior AI Architect.
2.  **Architecture (Modular Monolith)**:
    - **Single Container**: `ai-agent-platform`.
    - **No Microservices**: Forbid creating new services without approval.
    - **Directory Structure**: Explain `src/core`, `src/orchestrator`, `src/modules`.
3.  **State Management**:
    - **Context**: Persistent environment (e.g., 'default', 'git_repo').
    - **Conversation**: Linked to Context.
    - **Session**: Active interaction loop.
4.  **Coding Standards**:
    - **Strict Typing**: `mypy --strict`. No `Any`.
    - **Surgical Editing**: Use `replacement_chunks` logic, don't overwrite full files unless necessary.
    - **Imports**: No circular deps. `Interfaces -> Orchestrator -> Core`.
5.  **Testing Strategy (The Pyramid)**:
    - **Level 1: Unit Tests**: Test tools/functions in isolation using `pytest`.
    - **Level 2: Agent Scenarios**: Test full flows using `MockLLM`. **MANDATORY** for logic changes.
    - **Level 3: Integration**: Real API calls (only in `tests/integration`).

## 4. Implementation Steps
1.  Create `src/core/tests/mocks.py`.
2.  Update `conftest.py`.
3.  Create `src/core/tests/test_agent_scenarios.py` and implement `test_run_tool_flow`.
4.  Run tests to verify green state.
5.  Rewrite `GEMINI.md`.

## 5. Verification
- `pytest services/agent/src/core/tests/test_agent_scenarios.py` must pass.
- `GEMINI.md` must be present and follow the new structure.
