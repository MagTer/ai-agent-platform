# SYSTEM CONTEXT: AI AGENT PLATFORM

## 🛑 CRITICAL INSTRUCTIONS (READ FIRST)
You are the **Senior AI Platform Architect** for this project.
Your primary directive is: **"Code First, Verify Always."**

**MANDATORY WORKFLOW:**
Before marking ANY task as complete, you MUST execute the Quality Assurance CLI.
> **Command:** `./stack check`

* If this command fails (red output), you **MUST** fix the errors before proceeding.
* Use `./stack check --no-fix` for CI mode (no auto-fix).
* **Ruff/Black:** Do not argue with the linter. Fix the code.
* **Mypy:** strict typing is enforced. No `Any`. Use `list[str]`, not `List[str]`.
* **Tests:** If you write logic, you MUST write a test.


## 🚑 TROUBLESHOOTING
* **Logs:** Check container logs via `./stack dev logs` or `docker logs <container> --tail 50`.
* **Quality checks:** Use `./stack check` for fast verification of code changes. See next section for available commands.

---

## 1. ARCHITECTURE: The Modular Monolith
We follow a strict dependency flow. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the authoritative Directory Structure & Rules.

### 1.1 Protocol-Based Dependency Injection
The `core/` layer uses **Protocol classes** to define interfaces. This enables clean dependency injection:

```python
# Protocols define interfaces (core/protocols/)
from core.protocols.embedder import IEmbedder
from core.protocols.fetcher import IFetcher

# Providers supply implementations (core/providers.py)
from core.providers import get_embedder, get_fetcher
```

**Key Protocols** (all in `core/protocols/`):
- `IEmbedder` — text embedding interface (`embedder.py`)
- `IFetcher` — web fetching interface (`fetcher.py`)
- `IRAGManager` — RAG pipeline interface (`rag.py`)
- `ICodeIndexer` — code indexing interface (`indexer.py`)
- `IPriceTracker` / `IPriceScheduler` — price tracking (`price_tracker.py`)
- `IOAuthClient` — OAuth flows (`oauth.py`)
- `IEmailService` — email sending (`email.py`)

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
*   **Version:** Python 3.11–3.12 (runtime: 3.12)
*   **Async:** All I/O is `async/await`. Use `httpx` for HTTP and `SQLAlchemy 2.0 async` for DB (not AsyncPG directly).
*   **Imports:** Absolute imports only.
    * ✅ `from core.db import models`
    * ❌ `from ..core import models`

### 3.2 Surgical Editing
*   **Read-Before-Write:** Always read the file content before applying a diff/edit.
*   **Preserve:** Do not remove comments or existing functionality unless explicitly asked.

### 3.3 Code Editing
*   **Read before write:** Always read the target file before editing. Never guess at existing code.

---

## 4. TESTING STRATEGY
We use `pytest`. You are required to maintain the **Testing Pyramid**.

### 4.1 Running Tests
```bash
# Full quality check (recommended) - Ruff, Black, Mypy, Pytest
./stack check

# Individual checks
./stack lint       # Ruff + Black only (fast)
./stack typecheck  # Mypy only
./stack test       # Pytest only

# CI mode (no auto-fix)
./stack check --no-fix
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

### 5.2 Diagnostics API
All diagnostic endpoints are under `/platformadmin/api/` and require `X-Api-Key` header:

```bash
KEY=$(grep AGENT_DIAGNOSTIC_API_KEY .env | cut -d= -f2)
BASE="https://agent-dev.falle.se/platformadmin/api"

# System health
curl -s -H "X-Api-Key: $KEY" $BASE/status | python3 -m json.tool

# Find recent errors
curl -s -H "X-Api-Key: $KEY" "$BASE/traces/search?status=ERR&limit=10"

# Investigate a specific request
curl -s -H "X-Api-Key: $KEY" "$BASE/investigate/{trace_id}"
```

---

## 6. SELF-CORRECTION & DEBUGGING

### 6.1 Diagnostics API Reference
All access via Traefik — no direct host ports exposed.

| Endpoint | Purpose |
|----------|---------|
| `GET /platformadmin/api/status` | Health status (HEALTHY/DEGRADED/CRITICAL) |
| `GET /platformadmin/api/otel-metrics` | Error rate, avg latency, token usage |
| `GET /platformadmin/api/traces/search?status=ERR` | Recent error traces |
| `GET /platformadmin/api/investigate/{trace_id}` | Full trace + debug events + summary |
| `GET /platformadmin/api/debug/logs?trace_id=X` | Debug events for a trace |
| `GET /platformadmin/api/conversations` | Recent conversations |

### 6.2 Troubleshooting Workflow
```bash
KEY=$(grep AGENT_DIAGNOSTIC_API_KEY .env | cut -d= -f2)
BASE="https://agent-dev.falle.se/platformadmin/api"

# 1. Overall health
curl -s -H "X-Api-Key: $KEY" $BASE/status

# 2. Find failures
curl -s -H "X-Api-Key: $KEY" "$BASE/traces/search?status=ERR&limit=5"

# 3. Investigate specific trace
curl -s -H "X-Api-Key: $KEY" "$BASE/investigate/TRACE_ID_HERE"
```

### 6.3 Debug Events (OTel Spans)
Debug events are stored as OpenTelemetry span events in `data/spans.jsonl`.
Enabled via `SystemConfig.debug_enabled = "true"` in the admin portal (Diagnostics page).
There is NO `last_crash.log` or separate debug log file.

### 6.4 Agent Scenarios
* **MANDATORY:** Every feature flow needs a scenario test in `src/core/tests/test_agent_scenarios.py`.
* Use `MockLLMClient` to ensure deterministic execution.

---

## 7. CRITICAL CONSTRAINTS
1.  **NO SECRETS:** Never output API keys or credentials in chat.
2.  **INFRASTRUCTURE:** Do NOT edit `docker-compose.yml` without explicit user approval.
3.  **LIBRARIES:** Do NOT add new `pip` dependencies without checking if a standard library alternative exists.

## 7.1 GIT SAFETY
**NEVER use these commands — they destroy uncommitted work:**
```bash
git reset --hard   # FORBIDDEN
git stash          # FORBIDDEN — stashes get lost and forgotten
git checkout .     # FORBIDDEN
git clean -f       # FORBIDDEN
git push --force   # FORBIDDEN
```

**Safe pattern:**
```bash
git status                    # Always check first
# If dirty: commit all changes (even unrelated) before switching branches
git commit -am "wip: save work before context switch"
git pull --rebase origin main # Safe sync
```

**Alembic revision IDs must be ≤32 characters** (PostgreSQL `alembic_version` varchar(32) limit).
Example: `20260224_skill_proposals` ✅ — `20260224_add_skill_improvement_proposals` ❌

---

## 8. QUICK REFERENCE

### Common Commands
```bash
# Run quality check (lint, format, typecheck, test)
./stack check

# Run specific test file
python -m pytest src/core/tests/test_skill_delegate.py -v

# Run typecheck only
./stack typecheck

# Run linting and formatting only
./stack lint

# Run tests only
./stack test

# CI mode (no auto-fix)
./stack check --no-fix
```

### Important Paths
| Path | Purpose |
|------|---------|
| `core/protocols/` | Protocol definitions for DI (`IEmbedder`, `IFetcher`, etc.) |
| `core/providers.py` | Implementation providers |
| `core/observability/error_codes.py` | Structured error codes |
| `core/observability/debug_logger.py` | Debug events as OTel span events |
| `core/runtime/skill_quality.py` | Self-healing skill quality analyser |
| `core/tests/mocks.py` | Test mocks (`MockLLMClient`, `InMemoryAsyncSession`) |
| `core/db/models.py` | All DB models incl. `SkillImprovementProposal`, `SkillQualityRating` |
| `config/tools.yaml` | Tool registration |
| `./stack` | Quality check CLI (lint, typecheck, test) |

---

**REMINDER:**
Run `./stack check` now if you have modified any code.