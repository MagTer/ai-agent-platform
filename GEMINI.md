# Project Context & Developer Guide for Gemini

This document serves as the primary source of truth for the AI Agent (Gemini) when working on this repository. It defines the architectural standards, workflow protocols, and strict coding guidelines that must be followed.

## 1. Tech Stack & Environment
- **Platform:** Local "Universal Agent" platform (Privacy-first, containerized).
- **Language:** Python 3.11+
- **Web Framework:** FastAPI (Async/Await is **mandatory** for I/O).
- **Data Models:** Pydantic V2 (Strict typing, `model_config`, `field_validator`).
- **Database:** SQLite (State) & Qdrant (Vector Memory).
- **Package Manager:** Poetry.
- **Containerization:** Docker & Docker Compose.

## 2. Coding Standards & Style Guide (STRICT)

All generated code **MUST** adhere to the following configuration to pass the project's automated quality gates (`scripts/code_check.py`).

### A. Formatting (Black)
- **Line Length:** 100 characters.
- **Quote Style:** Double quotes (`"`) for strings.
- **Trailing Commas:** Enforced where applicable.
- **Exclusions:** Respect `.gitignore` and common build directories.

### B. Linting (Ruff)
- **Target Version:** Python 3.11 (`py311`).
- **Line Length:** 100 characters.
- **Selected Rules:**
  - `E`, `W` (Pycodestyle errors/warnings)
  - `F` (Pyflakes)
  - `I` (Isort - Import sorting)
  - `B` (Flake8-bugbear)
  - `UP` (Pyupgrade)
  - `S` (Flake8-bandit - Security)
  - `N` (Pep8-naming)
- **Import Sorting:** Standard Library -> Third Party -> Local Application.

### C. Type Safety (Mypy)
- **Strictness:** High.
- **No `Any`:** Avoid `Any` strictly. Use Pydantic models, `TypedDict`, or Generics.
- **Signatures:** **ALL** function arguments and return values MUST have type hints.
- **Configuration:**
  - `check_untyped_defs = true`
  - `disallow_untyped_defs = true`
  - `disallow_incomplete_defs = true`
  - `no_implicit_optional = true`

## 3. Workflow & Architecture

### A. Anti-Spaghetti Architecture
**Architect before you implement.**
1.  **Reuse:** Check `src/core/` for existing helpers before writing new ones.
2.  **Separation:**
    - `src/interfaces/`: HTTP/API layers only. NO business logic.
    - `src/orchestrator/` & `src/agent/`: Core business logic.
    - `src/core/`: Shared utilities, base classes, and domain models.
3.  **File Granularity:** Keep files focused. If a file exceeds ~200 lines, propose a split.
4.  **Dependency Direction:** Interfaces -> Orchestrator -> Agent/Core. Never import "upwards".

### B. Git Strategy
1.  **Branching:** Always use a new branch (`feature/...`, `fix/...`).
2.  **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`).
3.  **PRs:** Use `gh pr create` when ready.

### C. Implementation Process
1.  **Plan:** Outline the file structure and Pydantic models first.
2.  **Dependencies:** Use `poetry add` (never `pip install`).
3.  **Code:** Implement with strict typing and async patterns.
4.  **Verify:** Run the quality gate script immediately after implementation.

## 4. Verification (The Quality Gate)

**Do not** run `pytest` or `ruff` manually. Use the unified script which handles environments and paths:

```bash
python3 scripts/code_check.py
```

**Protocol:**
1.  Run the script.
2.  If it fails, read the output.
3.  Fix the specific linting/typing errors.
4.  Re-run until `✅ All quality checks completed successfully`.

## 5. Troubleshooting & Operations

- **Diagnostics:** Run `python scripts/troubleshoot.py` (via `poetry run` if needed) to collect platform logs.
- **Documentation:**
  - Architecture: `docs/architecture/`
  - Skills: `skills/` (Follow `docs/SKILLS_FORMAT.md`)