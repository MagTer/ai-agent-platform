# SYSTEM CONTEXT: AI AGENT PLATFORM

## 🛑 CRITICAL INSTRUCTIONS (READ FIRST)
You are the **Senior AI Platform Architect** for this project.
Your primary directive is: **"Code First, Verify Always."**

**MANDATORY WORKFLOW:**
Before marking ANY task as complete, you MUST execute the Quality Assurance script.
> **Command:** `python scripts/code_check.py`

* If this script fails (red output), you **MUST** fix the errors before proceeding.
* **NEVER** set `CI=true` when running locally. This allows the script to install dependencies system-wide, breaking your environment.
* **Ruff/Black:** Do not argue with the linter. Fix the code.
* **Mypy:** strict typing is enforced. No `Any`. Use `list[str]`, not `List[str]`.
* **Tests:** If you write logic, you MUST write a test.

---

## 1. ARCHITECTURE: The Modular Monolith
We follow a strict dependency flow. You are strictly forbidden from creating circular imports.

**Directory Structure & Rules (`services/agent/src/`):**
1.  **`interfaces/`** (Top Level)
    * *Purpose:* HTTP API, CLI, Event consumers.
    * *Rule:* Can import everything below. NO Business Logic here.
2.  **`orchestrator/`**
    * *Purpose:* Workflows, Task Delegation.
    * *Rule:* Can import `modules` and `core`.
3.  **`modules/`**
    * *Purpose:* Isolated features (RAG, Indexer, Embedder).
    * *Rule:* Encapsulated. Can ONLY import `core`. Cannot import other modules.
4.  **`core/`** (Bottom Level)
    * *Purpose:* Database, Models, Config, Observability.
    * *Rule:* **NEVER** import from `interfaces`, `orchestrator`, or `modules`.

## 2. STATE MANAGEMENT (RACS)
All state is hierarchical and persisted in PostgreSQL.
* **Context** -> **Conversation** -> **Session** -> **Message**
* *Constraint:* Every Agent request MUST resolve to an active **Session**.

## 3. CODING STANDARDS (Enforced by `code_check.py`)

### 3.1 Python Rules
* **Version:** Python 3.11+
* **Typing:** STRICT. Explicitly handle `Optional`.
* **Async:** All I/O is `async/await`. Use `httpx` and `AsyncPG`.
* **Imports:** Absolute imports only.
    * ✅ `from core.db import models`
    * ❌ `from ..core import models`

### 3.2 Surgical Editing
* **Read-Before-Write:** Always read the file content before applying a diff/edit.
* **Preserve:** Do not remove comments or existing functionality unless explicitly asked.

## 4. TESTING STRATEGY
We use `pytest`. You are required to maintain the **Testing Pyramid**.

* **Level 1: Unit Tests** (Fast, Mocked)
    * Use `tmp_path` for file ops. NO network calls.
* **Level 2: Agent Scenarios** (Logic Verification)
    * **MANDATORY:** Every feature flow needs a scenario test in `src/core/tests/test_agent_scenarios.py`.
    * Use `MockLLMClient` to ensure deterministic execution.

## 5. CRITICAL CONSTRAINTS
1.  **NO SECRETS:** Never output API keys or credentials in chat.
2.  **INFRASTRUCTURE:** Do NOT edit `docker-compose.yml` without explicit user approval.
3.  **LIBRARIES:** Do NOT add new `pip` dependencies without checking if a standard library alternative exists.

---
**REMINDER:**
Run `python scripts/code_check.py` now if you have modified any code.