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

## 🛠️ CROSS-PLATFORM DEVELOPMENT (WSL)
If operating from Windows but code resides in WSL, you MUST run checks via:
```
wsl -d Ubuntu --cd /home/magnus/dev/ai-agent-platform bash -c "python3 scripts/code_check.py"
```

## 🚑 TROUBLESHOOTING
* **Logs:** If you encounter issues, always check `services/agent/stack_up.log` using the `File Fetcher` before making assumptions.

---

## 1. ARCHITECTURE: The Modular Monolith
We follow a strict dependency flow. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the authoritative Directory Structure & Rules.


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

### 3.3 Code Editing
* **Use:** Always use Context7 to get code suggestions before editing.


## 4. TESTING STRATEGY
We use `pytest`. You are required to maintain the **Testing Pyramid**.

### 4.1 Running Integration Tests
To run integration tests with a live agent instance, use:
```bash
wsl -d Ubuntu --cd /home/magnus/dev/ai-agent-platform bash -c "python3 scripts/test_integration.py"
```

### 4.2 Test Layers
* **Level 1: Unit Tests** (Fast, Mocked)
    * Use `tmp_path` for file ops. NO network calls.

## 5. SELF-CORRECTION & DEBUGGING
The Agent can debug its own runtime errors.
* **Crash Logs**: Unhandled exceptions are written to `services/agent/last_crash.log`.
* **Reading Logs**: Use the `read_file` tool to inspect this log.
    * Path: `services/agent/last_crash.log` (Relative to service root)

* **Level 2: Agent Scenarios** (Logic Verification)
    * **MANDATORY:** Every feature flow needs a scenario test in `src/core/tests/test_agent_scenarios.py`.
    * Use `MockLLMClient` to ensure deterministic execution.

## 6. CRITICAL CONSTRAINTS
1.  **NO SECRETS:** Never output API keys or credentials in chat.
2.  **INFRASTRUCTURE:** Do NOT edit `docker-compose.yml` without explicit user approval.
3.  **LIBRARIES:** Do NOT add new `pip` dependencies without checking if a standard library alternative exists.

---
**REMINDER:**
Run `python scripts/code_check.py` now if you have modified any code.