---
focus: quality
generated: 2026-03-28
---

# Coding Conventions

**Analysis Date:** 2026-03-28

## Python Version & Type Annotations

**Runtime:** Python 3.12 in production; pyproject.toml targets `>=3.11,<3.13`.

**Future annotations:** Use `from __future__ import annotations` at the top of every module. About 70% of files already include it (112 of 161 Python files in `src/core/`). All new files must include it.

**Generic types:** Always use lowercase built-in generics. Never import `List`, `Dict`, `Tuple`, `Optional` from `typing`.

```python
# Correct
def process(items: list[str]) -> dict[str, int]: ...
def find(value: str | None = None) -> list[str]: ...

# Wrong - do not use
from typing import List, Dict, Optional
def process(items: List[str]) -> Dict[str, int]: ...
```

**No `Any`:** Never use `typing.Any` in production code. Specify concrete types. When the type is genuinely dynamic (e.g., JSON payloads), use `dict[str, object]` or define a TypedDict. The `Any` import appears in test files and mock infrastructure only, never in `src/core/` production modules.

**All functions must be typed:** mypy is configured with `disallow_untyped_defs = true` and `disallow_untyped_calls = true`. Every parameter and return value must have type annotations, including `-> None` for procedures.

## Async-First

**All I/O must be async.** No blocking calls in async context. FastAPI routes, database queries, LLM calls, HTTP fetches, and file I/O all use `async def`.

```python
# Correct
async def fetch_context(context_id: UUID, session: AsyncSession) -> Context | None:
    result = await session.execute(select(Context).where(Context.id == context_id))
    return result.scalar_one_or_none()

# Wrong
def fetch_context(context_id: UUID) -> Context | None:
    return db.query(Context).filter(Context.id == context_id).first()
```

## Import Style

**Absolute imports only.** Relative imports (starting with `.`) are forbidden in all modules except `__init__.py` barrel files.

```python
# Correct
from core.auth.credential_service import CredentialService
from shared.models import AgentRequest, StepOutcome

# Wrong - relative import
from .credential_service import CredentialService
```

**Known first-party packages** (configured in ruff isort): `core`, `modules`, `orchestrator`, `interfaces`, `stack`.

**Import order** (enforced by ruff isort):
1. `from __future__ import annotations`
2. Standard library
3. Third-party packages
4. First-party (`core`, `shared`, `interfaces`, etc.)

## Naming Conventions

**Files:** `snake_case.py` for all Python files. Test files prefixed with `test_`. Example: `test_planner_agent.py`, `credential_service.py`.

**Classes:** `PascalCase`. Abstract base classes are prefixed with `I` for interfaces/protocols (e.g., `IEmailService`, `IEmbedder`) or use the ABC suffix. Agents use `Agent` suffix: `PlannerAgent`, `StepExecutorAgent`, `StepSupervisorAgent`.

**Functions and methods:** `snake_case`. Private helpers prefixed with `_` (e.g., `_sanitize_user_input`, `_run_tool_gen`).

**Constants:** `UPPER_SNAKE_CASE` (e.g., `MAX_PROMPT_LENGTH`).

**Test functions:** Pattern `test_<action>_<scenario>_<expected_result>`:
```python
def test_parse_response_with_missing_field_raises_error(): ...
def test_execute_step_returns_retry_on_timeout(): ...
def test_valid_plan_passes(): ...
```

**Test classes:** `TestPascalCase` grouping related test functions:
```python
class TestPlannerAgent:
    def test_generates_plan_with_single_step(self) -> None: ...
```

## Formatting (Black + Ruff)

**Line length:** 100 characters (both Black and Ruff).

**Target version:** `py311` for both Black and Ruff.

**Black** handles all formatting. Never manually format; always let Black apply.

**Ruff** handles linting. Active rule sets: `E` (pycodestyle errors), `F` (pyflakes), `I` (isort), `B` (flake8-bugbear), `UP` (pyupgrade), `S` (bandit security), `N` (pep8-naming).

Ignored rules:
- `S101` - `assert` allowed (test code)
- `S104` - binding to `0.0.0.0` allowed (container deployment)
- `B008` - function calls in default arguments allowed (FastAPI `Depends`)
- `PLR0912`, `PLR0915` - branch and statement count not enforced via Ruff (McCabe limit used instead)

**McCabe complexity limit:** 18 (configured in `[tool.ruff.lint.mccabe]`). Functions with cyclomatic complexity above 18 must be refactored.

## Mypy Strictness

Configured in `services/agent/pyproject.toml` under `[tool.mypy]`:

- `check_untyped_defs = true`
- `disallow_untyped_defs = true`
- `disallow_incomplete_defs = true`
- `disallow_untyped_calls = true`
- `no_implicit_optional = true`
- `warn_redundant_casts = true`
- `warn_unused_ignores = true`
- `disable_error_code = ["import-untyped"]`

Mypy checks: `src/core`, `src/orchestrator`, `src/interfaces`, `src/stack`, `src/modules`.

When a `# type: ignore` is necessary, it must include the specific error code: `# type: ignore[return-value]`. Bare `# type: ignore` is not acceptable. There are currently 75 type-ignore annotations in `src/`; avoid adding new ones.

**Overrides** (errors suppressed): `typer`, `typer.*`, `yaml`, `aiogram`, `aiogram.*`.

## S105 False Positive Suppression

The bandit rule S105 flags string literals containing "token" as potential hardcoded secrets. This is a known false positive for enum values and column names. Suppress with inline comment:

```python
RAW_TOKEN = "raw_token"  # noqa: S105
telegram_bot_token: str | None = Field(...)  # noqa: S105
```

Do not suppress S105 without this explanatory comment.

## Architectural Rules

**4-layer modular monolith enforced by `.architecture-baseline.json`:**

```
interfaces/  ->  orchestrator/  ->  modules/  ->  core/
```

- `core/` never imports from any layer above it
- `modules/` never imports from other modules (use Protocol-based DI via `core/protocols/`)
- `interfaces/` may import from all lower layers
- Cross-module communication uses typed Protocol interfaces defined in `core/protocols/`

**Protocol-based dependency injection:** When a module needs a capability owned by another module, import its Protocol from `core/protocols/`, not the implementation.

**No direct database access from `interfaces/`:** Database operations go through service classes in `core/runtime/` or module services.

## Language Rules

**English everywhere** in code:
- All Python identifiers, comments, docstrings
- All HTML templates, JavaScript, CSS
- All YAML config files (`config/tools.yaml`, skill `.md` files)
- All Alembic migration messages
- All commit messages and PR descriptions
- All admin portal UI text

**Swedish only** for end-user chat responses (bot messages delivered to end users via Telegram or web chat). The agent responds in Swedish to user queries. This is a product requirement, not a code convention.

## HTML Template Rules

**Separation threshold:** When an admin portal module exceeds 500 lines of HTML or 40 KB total file size, extract the HTML into a dedicated template file.

**Template location:** `services/agent/src/interfaces/http/templates/*.html`

**Loading pattern:**
```python
template_path = Path(__file__).parent / "templates" / "admin_mcp.html"
content = template_path.read_text()
```

**Template files are source files** — always stage them before committing. Do not treat `.html` files as build artifacts.

## Security Conventions

**No credentials in code or logs.** Never log API keys, tokens, OAuth secrets, or passwords. Never include them in exception messages or tracebacks exposed to users.

**Fernet encryption for stored secrets.** OAuth tokens and credentials stored in the database are encrypted at rest using Fernet (`cryptography` library). When decrypting, always include a plaintext fallback for pre-encryption records.

**SSRF protection.** HTTP fetchers in `core/modules/fetcher/` validate redirect targets to prevent server-side request forgery (see `test_fetcher_ssrf.py`).

**Input sanitization.** User input passed to LLM prompts must be sanitized to remove prompt injection vectors. See `_sanitize_user_input` in `core/agents/planner.py`.

**No `eval()` or `exec()`** in production code. Subprocess calls require bandit S603 suppression with a documented justification (`src/core/models/gemini_cli.py`, `src/stack/checks.py` are the only approved locations).

**CSP headers** on all HTTP responses via middleware in `interfaces/http/app.py`.

**Rate limiting** via `slowapi` on public-facing endpoints.

## Alembic Migration Rules

**Revision IDs must be 32 characters or fewer** (PostgreSQL `varchar(32)` limit).

Migration files live in `services/agent/alembic/versions/*.py` and are treated as source files — always stage them before committing.

## Docstrings and Comments

**Module docstrings:** Every test file and major module should have a top-level docstring describing its purpose.

**Function docstrings:** Required for public API methods. Single-line for simple functions, multi-line for complex ones.

**Inline comments:** Use sparingly. Comments should explain *why*, not *what*. Code should be self-documenting.

## Dependency Management

**Check stdlib alternatives first.** Before adding a new pip dependency, verify no standard library equivalent exists.

**Never edit `docker-compose.yml`** without explicit user approval.

**Poetry** is the package manager. Lockfile (`poetry.lock`) is committed and must stay in sync.

---

*Convention analysis: 2026-03-28*
