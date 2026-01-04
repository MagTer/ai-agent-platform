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


## 🚑 TROUBLESHOOTING
* **Logs:** If you encounter issues, always check `services/agent/stack_up.log` using the `File Fetcher` before making assumptions.

---

## 1. ARCHITECTURE: The Modular Monolith
We follow a strict dependency flow. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the authoritative Directory Structure & Rules.

### 1.1 Protocol-Based Dependency Injection
The `core/` layer uses **Protocol classes** to define interfaces. This enables clean dependency injection:

```python
# Protocols define interfaces (core/protocols/)
from core.protocols import EmbedderProtocol, MemoryProtocol

# Providers supply implementations (core/providers.py)
from core.providers import get_embedder, get_memory_store
```

**Key Protocols:**
- `EmbedderProtocol`: Text embedding interface
- `MemoryProtocol`: Vector memory store interface
- `LLMProtocol`: LLM client interface
- `ToolProtocol`: Tool execution interface

## 2. STATE MANAGEMENT (RACS)
All state is hierarchical and persisted in PostgreSQL.
* **Context** -> **Conversation** -> **Session** -> **Message**
* *Constraint:* Every Agent request MUST resolve to an active **Session**.

## 📝 CODING STANDARDS

### Philosophy: Strict Types, Pragmatic Logic
*   **Strict Types:** `mypy` is strict. Always use type hints. No `Any`.
*   **Pragmatic Logic:** Complexity is allowed if readability is maintained (McCabe < 18).
*   **No Band-Aids:** Do not use `# noqa` unless absolutely necessary; rely on the relaxed config instead.

### 3.1 Python Rules
*   **Version:** Python 3.11+
*   **Async:** All I/O is `async/await`. Use `httpx` and `AsyncPG`.
*   **Imports:** Absolute imports only.
    * ✅ `from core.db import models`
    * ❌ `from ..core import models`

### 3.2 Surgical Editing
*   **Read-Before-Write:** Always read the file content before applying a diff/edit.
*   **Preserve:** Do not remove comments or existing functionality unless explicitly asked.

### 3.3 Code Editing
*   **Use:** Always use Context7 to get code suggestions before editing.

---

## 4. TESTING STRATEGY
We use `pytest`. You are required to maintain the **Testing Pyramid**.

### 4.1 Running Tests
```bash
# Full quality check (recommended)
python3 scripts/code_check.py

# Integration tests with live agent
python3 scripts/test_integration.py
```

### 4.2 Test Layers
* **Level 1: Unit Tests** (Fast, Mocked)
    * Use `tmp_path` for file ops. NO network calls.
    * Use `MockLLMClient` from `core/tests/mocks.py`
    * Use `InMemoryAsyncSession` for database tests

* **Level 2: Integration Tests** (Real DB, Mocked LLM)
    * Test full request flows
    * Verify database interactions

* **Level 3: Semantic/Golden Master Tests** (Slow, Real)
    * Run against real/mocked LLM to verify reasoning.
    * Config: `services/agent/tests/semantic/golden_queries.yaml`
    * Command: `python services/agent/scripts/run_semantic_eval.py`

### 4.3 Key Test Files
| File | Coverage |
|------|----------|
| `test_skill_delegate.py` | Skill execution, streaming, error handling |
| `test_openwebui_adapter.py` | HTTP adapter formatting |
| `test_error_codes.py` | Error classification |
| `test_agent_scenarios.py` | End-to-end flows |

---

## 5. OBSERVABILITY

### 5.1 Structured Error Codes
Use standardized error codes from `core/observability/error_codes.py`:

```python
from core.observability.error_codes import (
    ErrorCode,
    classify_exception,
    format_error_for_ai,
)

# Classify an exception
code = classify_exception(exc)  # Returns ErrorCode.LLM_RATE_LIMITED

# Format for AI consumption
info = format_error_for_ai(code)
# {
#   "error_code": "LLM_RATE_LIMITED",
#   "severity": "warning",
#   "recovery_hint": "Wait and retry with exponential backoff"
# }
```

### 5.2 Diagnostics Endpoint
**`GET /diagnostics/summary`** returns AI-optimized health report:
- `overall_status`: HEALTHY | DEGRADED | CRITICAL
- `failed_components`: List with error codes and recovery hints
- `recommended_actions`: Prioritized list of fixes

---

## 6. SELF-CORRECTION & DEBUGGING
The Agent can debug its own runtime errors using the diagnostics APIs.

### 6.1 Diagnostics API Reference
Use these endpoints for autonomous troubleshooting:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/diagnostics/summary` | GET | AI-optimized health report with error codes and recovery hints |
| `/diagnostics/crash-log` | GET | Read `last_crash.log` content via API (no file access needed) |
| `/diagnostics/run` | POST | Run integration tests on all components |
| `/diagnostics/traces` | GET | Get recent traces (add `?show_all=true` to include health checks) |
| `/diagnostics/metrics` | GET | System health metrics with error hotspots |

### 6.2 Troubleshooting Workflow
1. **Check Health Status First:**
   ```bash
   curl http://localhost:8000/diagnostics/summary
   ```
   Returns `overall_status` (HEALTHY | DEGRADED | CRITICAL) and `recommended_actions`.

2. **Read Crash Logs:**
   ```bash
   curl http://localhost:8000/diagnostics/crash-log
   ```
   Returns `{ "exists": true/false, "content": "...", "modified": "ISO timestamp" }`

3. **Search Specific Traces:**
   The diagnostics dashboard (`/diagnostics/`) includes:
   - TraceID search box for filtering
   - "Show diagnostic/health traces" toggle
   - Span waterfall visualization

4. **Run Integration Tests:**
   ```bash
   curl -X POST http://localhost:8000/diagnostics/run
   ```
   Tests: LiteLLM, Qdrant, PostgreSQL, SearXNG, Embedder, Internet, Workspace.

### 6.3 Crash Logs
* **Crash Logs**: Unhandled exceptions are written to `services/agent/last_crash.log`.
* **API Access**: Use `GET /diagnostics/crash-log` instead of file system access.
* **Path**: `services/agent/last_crash.log` (Relative to service root)

* **Agent Scenarios**: (Logic Verification)
    * **MANDATORY:** Every feature flow needs a scenario test in `src/core/tests/test_agent_scenarios.py`.
    * Use `MockLLMClient` to ensure deterministic execution.

---

## 7. CRITICAL CONSTRAINTS
1.  **NO SECRETS:** Never output API keys or credentials in chat.
2.  **INFRASTRUCTURE:** Do NOT edit `docker-compose.yml` without explicit user approval.
3.  **LIBRARIES:** Do NOT add new `pip` dependencies without checking if a standard library alternative exists.

---

## 8. QUICK REFERENCE

### Common Commands
```bash
# Run quality check
python scripts/code_check.py

# Run specific test file
python -m pytest src/core/tests/test_skill_delegate.py -v

# Run mypy only
python -m mypy

# Run ruff only
python -m ruff check .
```

### Important Paths
| Path | Purpose |
|------|---------|
| `core/protocols/` | Protocol definitions for DI |
| `core/providers.py` | Implementation providers |
| `core/observability/error_codes.py` | Structured error codes |
| `core/tests/mocks.py` | Test mocks (MockLLMClient, InMemoryAsyncSession) |
| `scripts/code_check.py` | Quality assurance script |

---

**REMINDER:**
Run `python scripts/code_check.py` now if you have modified any code.