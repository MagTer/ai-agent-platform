# Agent Architecture & Workflow Guide

## 1. Project Context & Stack
- **Platform:** Local "Universal Agent" platform (Privacy-first, containerized).
- **Core Stack:**
  - **Lang:** Python 3.10+
  - **Web Framework:** FastAPI (Async/Await mandatory for I/O).
  - **Data Models:** Pydantic V2 (Strict typing, `model_config`, `field_validator`).
  - **Database:** SQLite (State) & Qdrant (Vector Memory).
  - **Package Manager:** Poetry.
- **Environment Tools:**
  - `git`: Available for version control.
  - `gh`: GitHub CLI available for PR management.
  - `make` / `scripts`: Automation scripts in root.

## 2. Critical Workflow Rules (Mandatory)

### A. Git & Branching Strategy
**Main Branch Protection is ACTIVE.** You cannot push to `main`.
1.  **Start:** Always create a new branch: `git checkout -b feature/short-description` or `fix/issue-desc`.
2.  **Commit:** Make frequent, atomic commits with conventional messages (e.g., `feat: add memory vector store`, `fix: typing in router`).
3.  **Push:** Push to origin: `git push -u origin feature/name`.
4.  **PR:** When a task is complete and verified, use `gh` to open a PR:
    
    gh pr create --title "feat: Description" --body "Summary of changes..."

### B. Anti-Spaghetti Architecture (Architect First)
**Before writing code, you must plan the structure.**
1.  **Leverage Context:**
    - Scan existing files in `src/` to match patterns and reuse utilities.
    - DO NOT reinvent wheels if a helper function already exists in `src/core`.
2.  **Separation of Concerns:**
    - `src/interfaces/`: HTTP/API layers only. NO business logic here.
    - `src/orchestrator/` & `src/agent/`: Core business logic.
    - `src/core/`: Shared utilities and base classes.
3.  **File Granularity:**
    - **One Class per File:** Generally, keep files focused on a single responsibility.
    - **Size Limit:** If a file approaches ~200 lines, pause and propose a refactor/split.
4.  **Dependency Direction:**
    - Interfaces -> Orchestrator -> Agent/Core.
    - NEVER import "upwards" (e.g., Core logic relying on HTTP Interface types).

### C. Implementation Strategy
1.  **Thinking Process:** Before outputting code, briefly outline your plan (files to create, Pydantic models to define) to ensure structural integrity.
2.  **Plan:** Outline the file structure and Pydantic models (contracts) first.
3.  **Dependencies:** If new libs are needed, use `poetry add <lib>`. NEVER use `pip install`.
4.  **Code:** Implement logic ensuring strict type safety.
5.  **Verify:** Run checks immediately.

## 3. Code Quality & Strictness
**Act as if a strict CI pipeline runs on every generation.**

- **Linting & Formatting:**
  - Code must pass `ruff` (linting) and `black` (formatting).
  - Use double quotes `"` for strings.
  - Sort imports: Stdlib -> Third Party -> Local.
- **Type Safety (Strict Mypy):**
  - **NO `Any`:** Avoid `Any` strictly. Define proper Pydantic models or TypedDicts.
  - **Signatures:** All function arguments and return values MUST have type hints.
- **Error Handling:**
  - Catch specific exceptions (e.g., `ValueError`, `httpx.RequestError`).
  - Never use bare `except:`.

## 4. Verification & Testing (Pre-Commit)

**Running Tests & Quality Checks**
DO NOT run `pytest` or `poetry run` directly. Always use the unified entry point which handles `PYTHONPATH` and directories for you:

    python scripts/code_check.py

**Auto-Correction Protocol:**
1. If the script fails, read the error log.
2. Attempt to fix the linting/typing errors automatically.
3. Re-run the script.
4. Only ask the user for help if you cannot resolve the error after 2 attempts.

## 5. Debugging & Diagnostics (Runtime)
If you encounter runtime errors, connection refusals, or unexpected behavior during manual testing or usage:

1.  **Run Diagnostics:**
    
    poetry run python scripts/troubleshooting.py
    
    *This script collects logs from platform components (Agent, Qdrant, SQLite, etc.).*

2.  **Analyze:** Read the collected logs to identify root causes (e.g., DB locked, container down) before modifying code.

## 6. Documentation
- Update `docs/` if architecture changes.
- If adding a new Skill:
  - Create the definition in `skills/`.
  - Follow `docs/SKILLS_FORMAT.md`.