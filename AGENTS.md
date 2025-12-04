# Agent Guide

## Project Overview

- **Platform**: A local, containerised "Universal Agent" platform designed for privacy, flexibility, and actionable skills.
- **Stack**:
  - **Orchestrator/Agent**: Python FastAPI service (`src/`).
  - **LLM Gateway**: LiteLLM (handling provider abstractions).
  - **Memory**: Qdrant (Vector Store) and SQLite (State Store).
  - **UI**: Open WebUI (interacting via OpenAI-compatible API).
  - **CLI**: `stack` tool (Typer-based) for lifecycle management (`python -m stack`).
- **Key Concepts**:
  - **Skills**: Markdown-defined capabilities in `skills/` with YAML frontmatter.
  - **Tools**: Python classes implementing specific logic (e.g., `web_fetch`, `memory`).
  - **Planning**: Two-stage execution (Plan -> Execute) for complex tasks.

## Git Workflow

- Create short-lived feature branches (`feature/<desc>`, `bugfix/<issue>`, etc.) off `main`.
- Keep commits focused (code + matching docs/tests). Split large efforts into reviewable slices.
- Reference relevant docs in PR descriptions.
- **Commit Messages**: Use conventional commits (e.g., `feat: ...`, `fix: ...`).

## Build & Test

Run the following from the repo root before submitting a PR. The `scripts/code_check.py` script is your primary validation tool.

1.  **Install Dependencies**:
    ```bash
    poetry install
    ```

2.  **Run Comprehensive Checks**:
    ```bash
    poetry run python scripts/code_check.py
    ```
    *This runs Ruff (lint/format), Black, Mypy (types), and Pytest.*

3.  **Manual Verification (if needed)**:
    - Type Checking: `poetry run mypy src`
    - Tests: `poetry run pytest`

## Code Style & Quality

- **Python**: Formatted with Black and linted via Ruff.
- **Typing**: Strict type hints required (checked by `mypy`).
- **Structure**:
  - Core logic in `src/core/`.
  - Agent implementations in `src/agent/` (or `src/core/agents/`).
  - HTTP interfaces in `src/interfaces/`.
  - Orchestrator logic in `src/orchestrator/`.
- **Conventions**:
  - Use Pydantic v2 models.
  - Prefer `async/await` for I/O bound operations.
  - Keep "Skills" logic declarative in Markdown where possible.

## Documentation Expectations

- **Root `README.md`**: High-level project entry point.
- **`docs/`**: The single source of truth for architecture and process.
  - `docs/contributing.md`: Detailed workflow rules.
  - `docs/PROJECT_PROFILE.md`: Vision and constraints.
  - `docs/SKILLS_FORMAT.md`: Specification for defining new skills.
  - `docs/architecture/`: Deep dives into specific subsystems.
- **Update Rule**: If code behavior changes, the corresponding documentation **must** be updated in the same PR.

## Architecture Invariants

- **Agent Protocol**: The service accepts `AgentRequest` and returns `AgentResponse` (with steps/trace).
- **Open WebUI Compatibility**: The adapter in `src/interfaces/http/openwebui_adapter.py` translates OpenAI Chat Completions to internal `AgentRequest`s.
- **Skills**: Must be defined in `skills/` with valid YAML frontmatter. The `SkillLoader` scans this directory dynamically.
- **Routing**: `Dispatcher` determines if a request is a generic chat or a specific skill command (starting with `/`).
- **State**: Conversation state is persisted in SQLite; Semantic memory in Qdrant.

## Boundaries & Safety

- **Secrets**: Never commit secrets. Use `.env` (and `.env.template`).
- **Testing**: All new features require unit tests.
- **Migrations**: Database schema changes must be handled carefully (currently SQLite/Qdrant).
- **Dependencies**: Manage via `poetry`. Update `pyproject.toml` and `poetry.lock` together.

Refer to `docs/contributing.md` for the broader collaboration workflow.
