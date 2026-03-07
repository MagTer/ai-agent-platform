# Comprehensive Platform Audit -- 2026-03-07

**Agents:** Gemini (Domains A-M), Opus Architecture, Opus Security, Opus Performance
**Branch:** fix/codeql-security-alerts
**Note:** Gemini CLI ran without `--all-files` (flag removed from this version) but performed tool-based file reads across the codebase.

---

## Executive Summary Table

| Domain | CRITICAL | HIGH | MEDIUM | LOW | Positive |
|--------|----------|------|--------|-----|----------|
| Architecture | 2 | 5 | 8 | 4 | 9 |
| Security | 0 | 0 | 2 | 5 | 33 |
| Performance | 0 | 3 | 6 | 5 | 10 |
| A: Documentation vs Code | 0 | 1 | 2 | 1 | Clear architectural vision |
| B: Component Functionality | 0 | 0 | 1 | 0 | Excellent orchestrator/skill maturity |
| C: Dead Code | 0 | 0 | 2 | 2 | Clean directory structure |
| D: Observability | 0 | 0 | 1 | 1 | Deep OTel integration |
| E: Testing | 0 | 0 | 1 | 1 | 50+ test files, strong core coverage |
| F: Stack CLI | 0 | 0 | 1 | 1 | Unified CLI improves DX |
| G: CI/CD | 0 | 0 | 1 | 1 | Trivy + pip-audit integrated |
| H: Arch Guards | 0 | 0 | 0 | 0 | ArchitectureValidator exemplary |
| I: Refactoring | 0 | 1 | 2 | 1 | Modular design |
| J: Configuration | 0 | 0 | 2 | 1 | Well-commented template |
| K: Admin Portal | 0 | 1 | 1 | 1 | Consistent UI, strong CSRF |
| L: Docker/Infra | 0 | 0 | 1 | 1 | Secure-by-default isolation |
| M: Database | 0 | 0 | 1 | 0 | Clean SQLAlchemy 2.0 models |
| **TOTALS** | **2** | **11** | **33** | **25** | **55+** |

**Overall posture:** The platform is production-ready with a mature security posture (0 CRITICAL/HIGH security findings). Architecture is sound but has a god class and token-in-logs issue. Performance has no critical blockers but several medium optimizations available.

---

## Top 20 Priority Fixes (Ranked by Risk x Effort)

| Rank | Severity | Domain | Finding | File:Line | Effort |
|------|----------|--------|---------|-----------|--------|
| 1 | CRITICAL | Architecture | OAuth bearer token logged at INFO level (20 chars exposed) | `core/mcp/client.py:209-213` | Low |
| 2 | CRITICAL | Architecture | ServiceFactory accesses private `pool._pools`, `pool._negative_cache` breaking encapsulation | `core/runtime/service_factory.py:132-147` | Medium |
| 3 | HIGH | Architecture | Raw exception messages (`str(e)`) returned to users | `orchestrator/dispatcher.py:111,122` | Low |
| 4 | HIGH | Architecture | AgentService god class: 1847 lines, 25+ methods | `core/runtime/service.py` | High |
| 5 | HIGH | Architecture | `shared/` and `utils/` layers undocumented in ARCHITECTURE.md | `docs/ARCHITECTURE.md` | Low |
| 6 | HIGH | Architecture | Dispatcher does direct SQLAlchemy DB operations, bypassing service layer | `orchestrator/dispatcher.py:236-294` | High |
| 7 | HIGH | Architecture | 71 production files use `typing.Any` violating coding standard | Various | Medium |
| 8 | HIGH | Performance | `command_loader.py` does blocking rglob+YAML parse on every request | `core/command_loader.py:40-107` | Low |
| 9 | HIGH | Performance | `inspect.getouterframes()` called per request in MemoryStore constructor | `core/runtime/memory.py:76-79` | Low |
| 10 | HIGH | Performance | `SkillExecutor._validated_contexts` cache is per-request (no cross-request benefit) | `core/skills/executor.py:78` | Low |
| 11 | MEDIUM | Security | OpenWebUI adapter auth bypass has no production environment guard | `interfaces/http/openwebui_adapter.py:52-54` | Low |
| 12 | MEDIUM | Security | Request body captured in OTel spans for admin portal (may contain credentials) | `interfaces/http/middleware.py:161-167` | Low |
| 13 | MEDIUM | Architecture | `admin_contexts.py` at 2452 lines -- needs splitting | `interfaces/http/admin_contexts.py` | High |
| 14 | MEDIUM | Architecture | Background tasks fire-and-forget without error callbacks | `service_factory.py:150`, `bootstrap.py:200,221` | Low |
| 15 | MEDIUM | Architecture | DB engine has no connection timeout | `core/db/engine.py` | Low |
| 16 | MEDIUM | Performance | `ToolPermission` query runs every request without caching | `core/runtime/service_factory.py:102-105` | Medium |
| 17 | MEDIUM | Performance | `ContextManager.__init__` calls blocking `mkdir` per request | `core/context_manager.py:28-29` | Low |
| 18 | MEDIUM | Performance | Per-context skill dir existence check -- blocking rglob per request | `core/runtime/service_factory.py:162-166` | Medium |
| 19 | MEDIUM | Security | HSTS header missing `preload` directive; absent on dev-with-TLS | `interfaces/http/middleware.py:96-97` | Low |
| 20 | MEDIUM | Gemini-C | `memory_writer.py` and `test_runner.py` implemented but not in `tools.yaml` (unreachable) | `services/agent/config/tools.yaml` | Low |

---

## Full Findings: Gemini Domains A-M

*Source: Gemini CLI codebase analysis*

### Domain A: Documentation vs Code

**HIGH -- `ARCHITECTURE.md` stale on credential scoping and ID types**
`ARCHITECTURE.md` still documents `UserCredential` as scoped to `user_id`, but migration `20260212_credentials_to_context_scope.py` moved it to `context_id`. Also documents IDs as `int` but all models use `UUID`. File: `docs/ARCHITECTURE.md` vs `core/db/models.py`.

**MEDIUM -- Slack and Discord listed as interfaces but not implemented**
`ARCHITECTURE.md` lists Slack and Discord interface adapters. No code exists for these in `interfaces/`.

**MEDIUM -- `encrypted_value` type mismatch in docs**
Documented as `LargeBinary` in `ARCHITECTURE.md`, implemented as `String` (hex-encoded Fernet) in `models.py`.

**LOW -- `User.name` in docs vs `User.display_name` in code**

---

### Domain B: Component Maturity

| Component | Maturity | Notes |
|-----------|----------|-------|
| Orchestrator | EXCELLENT | Highly deterministic, well-tested |
| Planner | EXCELLENT | Strong plan generation with supervision |
| Skill system | EXCELLENT | Scoped tool access, self-correction |
| LiteLLM client | HIGH | Shared singleton, async, connection pooled |
| MCP client | HIGH | TTL eviction, negative caching, async |
| SSE streaming | HIGH | Batched, yield points for event loop |
| Conversation management | HIGH | Full RACS hierarchy in PostgreSQL |
| Admin Portal | MEDIUM | Extensive features but business logic in HTTP layer |

**GAP:** Agent API rate limiting relies on slowapi but per-route `@limiter.limit()` decorators are sparse (see SEC-L5).

---

### Domain C: Dead Code

**MEDIUM -- Two tools implemented but not registered in `tools.yaml`**
`core/tools/memory_writer.py` and `core/tools/test_runner.py` exist but are missing from `config/tools.yaml`. Unreachable by any skill. File: `services/agent/config/tools.yaml`.

**MEDIUM -- Unused dependencies in `pyproject.toml`**
`aiogram` and `langchain-text-splitters` have no detected usage in `src/`. File: `services/agent/pyproject.toml`.

**LOW -- `QDRANT_COLLECTION` defined twice in `.env.template`**
Both `QDRANT_COLLECTION` and `AGENT_QDRANT_COLLECTION` exist. One is stale. File: `.env.template`.

---

### Domain D: Logging & Observability

**MEDIUM -- No per-conversation trace visualization in Admin Portal**
`trace_id` is persisted in the `Message` table, and `/platformadmin/api/investigate/{trace_id}` exists, but the Admin Portal conversation view does not link to or display per-message traces. File: `interfaces/http/admin_contexts.py`.

**LOW -- `SPAN_LOG_PATH` and `OTEL_*` env vars missing from `.env.template`**
These are used in code but not documented in the template.

---

### Domain E: Testing Gaps

**MEDIUM -- `pytest-testmon` incompatible with branch coverage; blind spots possible**
CI uses `pytest-testmon` for selective execution but it explicitly precludes branch coverage reporting.

**LOW -- `admin_*.py` HTTP handlers have significantly lower test coverage than `core/`**

---

### Domain F: Stack Script

**MEDIUM -- Env var inconsistency: `AGENT_LITELLM_API_BASE` vs `LITELLM_BASE_URL`**
`test_openrouter_models.py` uses `LITELLM_BASE_URL` but the standard env var is `AGENT_LITELLM_API_BASE`. File: `tests/integration/test_openrouter_models.py`.

**LOW -- `stack health` does not verify DB connectivity** (only checks HTTP status)

---

### Domain G: CI/CD Pipeline

**MEDIUM -- No post-deployment smoke tests in CI**
`stack deploy` runs smoke tests locally but no automated post-deployment verification job in GitHub Actions.

**LOW -- BuildKit not explicitly set in `ci.yml`** (enabled in `.env.template` only)

**Positive:** Trivy container scanning and `pip-audit` dependency auditing already integrated.

---

### Domain H: Agent Config & Architecture Guards

**EXCELLENT:** `ArchitectureValidator` (`core/validators/architecture.py`) strictly enforces the 4-layer dependency model and is integrated into `./stack check`. No findings.

---

### Domain I: Refactoring Opportunities

**HIGH -- `admin_contexts.py` (84KB) and `admin_price_tracker.py` (61KB) mix HTTP and business logic**
Complex service logic embedded in FastAPI route handlers. Should be extracted to `core/services/`.

**MEDIUM -- `admin_shared.py` contains 300+ lines of CSS as a Python string**
Should be a static `.css` file or Jinja2 template.

---

### Domain J: Configuration

**MEDIUM -- Vars used in code but missing from `.env.template`**
`SPAN_LOG_PATH`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` used in code but undocumented in template.

**MEDIUM -- `AGENT_DIAGNOSTIC_API_KEY` in template but no setup instructions**

**LOW -- `QDRANT_COLLECTION` duplicate in `.env.template`**

---

### Domain K: Admin Portal

**HIGH -- Business logic in HTTP handlers** (see Domain I)

**MEDIUM -- `admin_shared.py` CSS in Python string** (see Domain I)

**LOW -- No pagination on conversation/message lists**
Long-running contexts produce unbounded list responses.

**Positive:** Consistent UI via `admin_page_layout()`, strong CSRF on all mutations, XSS prevention via `html.escape()`.

---

### Domain L: Docker & Infrastructure

**MEDIUM -- No resource limits (mem_limit/cpus) on any container**
A runaway process can OOM the host.

**LOW -- Several images not pinned to digest** (tag-only references)

**Positive:** Network isolation between dev and prod. Traefik handles TLS. Healthchecks on all services.

---

### Domain M: Database & Migrations

**MEDIUM -- `ScheduledJob` has both `context_id` and `user_id` FKs with inconsistent query usage**
Some queries filter by `context_id`, others by `user_id`. Semantics unclear. File: `core/db/models.py`.

**Positive:** Clean SQLAlchemy 2.0 async models, comprehensive indices, UUID PKs, proper cascade rules, RACS hierarchy sound.

---

## Full Findings: Architecture

*Source: Opus Architecture Agent*

### CRITICAL

#### ARCH-C1: Bearer Token Logged at INFO Level
**File:** `core/mcp/client.py:209-213`

```python
LOGGER.info(
    "MCP %s: Using Bearer token (first 20 chars): %s...",
    self._name,
    auth_token[:20] if len(auth_token) > 20 else auth_token,
)
```

For tokens <=20 chars, the **entire token** is exposed. INFO logs are shipped to centralized logging. OWASP A3:2021.

**Fix:** Remove token content. Log only a boolean: `"MCP %s: Bearer token present: %s", self._name, bool(auth_token)`.

#### ARCH-C2: Private Member Access Across Class Boundaries
**File:** `core/runtime/service_factory.py:132-147, 233, 240`

`ServiceFactory` directly accesses `pool._pools`, `pool._negative_cache`, `pool._negative_cache_ttl` on `McpClientPool`. Creates tight coupling with no compile-time enforcement.

**Fix:** Add public methods to `McpClientPool`: `get_cached_clients()`, `should_skip_connection()`, `record_connection_failure()`.

---

### HIGH

#### ARCH-H1: Raw Exception Messages Returned to Users
**File:** `orchestrator/dispatcher.py:111,122`

```python
"content": f"Command usage error: {str(e)}",
"content": f"Failed to parse command: {str(e)}",
```

The `except Exception as e` block at line 116 can expose internal paths, class names, or DB errors. OWASP A7:2021.

**Fix:** Return generic user messages; log full exception server-side.

#### ARCH-H2: AgentService God Class
**File:** `core/runtime/service.py` (1847 lines, 25+ methods)

Thin delegating wrapper methods remain (lines 1735-1760). `execute_stream` alone (lines 1389-1584) orchestrates 5 phases. High cognitive load and merge conflict risk.

**Fix:** Extract `AgenticExecutor` for retry/replan logic. Remove backward-compat wrappers.

#### ARCH-H3: Undocumented Fifth Layer (`shared/`, `utils/`)
**File:** `docs/ARCHITECTURE.md`

Both `shared/` and `utils/` are imported by all layers but appear nowhere in the layer dependency matrix. `shared/` correctly has zero upward dependencies (verified), but this is not documented.

**Fix:** Add `shared/` as "Layer 0" to ARCHITECTURE.md with the explicit rule: "may never import from any other layer."

#### ARCH-H4: Dispatcher Does Direct DB Persistence
**File:** `orchestrator/dispatcher.py:236-294`

Creates `Conversation`, `Session`, and `Message` rows directly for "direct answer" responses, bypassing `ConversationPersistence`. Missing `trace_id` on messages created via this path.

**Fix:** Delegate to `AgentService.persist_direct_answer()` or `ConversationPersistence`.

#### ARCH-H5: 71 Production Files Use `typing.Any`
Violates the "Never use `Any`" coding standard. High-traffic instances: `shared/models.py:6`, `core/runtime/service.py:8`, `orchestrator/dispatcher.py:6`.

**Fix:** Audit and replace with `TypedDict` for structured metadata, `JsonValue` alias for JSON containers.

---

### MEDIUM

#### ARCH-M1: `core/routing/` Misplaced
`core/routing/unified_orchestrator.py` does orchestration logic (LLM calls, plan routing) but sits in `core/`. Should be `orchestrator/routing/`.

#### ARCH-M2: f-string Logging (119 Occurrences, 34 Files)
Bypasses lazy evaluation; potential log injection for user-controlled data not passed through `sanitize_log()`.

**Fix:** Use `%s`-style formatting: `LOGGER.error("Error: %s", e)`.

#### ARCH-M3: `admin_contexts.py` at 2452 Lines
54 functions spanning credentials, MCP, OAuth, skill quality, and more.

**Fix:** Split into `admin_context_credentials.py`, `admin_context_skills.py`, etc.

#### ARCH-M4: Background Task Fire-and-Forget
**Files:** `service_factory.py:150`, `bootstrap.py:200, 221`

Tasks created without stored references or done callbacks. Silent failures possible.

**Fix:** Store references in a set; add `.add_done_callback(log_task_exception)`.

#### ARCH-M5: No DB Connection Timeout
**File:** `core/db/engine.py`

No `connect_args` timeout. PostgreSQL unresponsiveness would hang indefinitely.

**Fix:** `connect_args={"server_settings": {"statement_timeout": "30000"}}`.

#### ARCH-M6: `bootstrap.py` Accesses Private `SchedulerAdapter._compute_next_run`
**File:** `interfaces/http/bootstrap.py:108`

**Fix:** Make `_compute_next_run` public.

#### ARCH-M7: `HealthStatus` Dual Import Path
Defined in `shared/models.py:153`, re-exported from `core/runtime/models.py`. Consumers import from different locations.

**Fix:** Standardize import path to `shared.models`.

#### ARCH-M8: `core/agents/` Contains Orchestration Logic
`PlannerAgent`, `StepExecutorAgent`, etc. are orchestration components, not core infrastructure.

**Note:** Low-impact given current imports are clean; document as intentional.

---

### LOW

#### ARCH-L1: `create_lifespan` Returns `Any` (`bootstrap.py:125`)
#### ARCH-L2: `orchestrator/dispatcher.py` Bare `list` Return Type (line 55)
#### ARCH-L3: Module-Level Global State in `mcp_loader.py`
#### ARCH-L4: `seed_system_context` Uses `/tmp` as Default CWD (`bootstrap.py:51`)

---

### Positive Findings (Architecture)

| # | Finding | File |
|---|---------|------|
| P1 | Protocol-based DI consistently applied via `core/protocols/` | `core/providers.py` |
| P2 | Comprehensive shutdown sequence -- all resources cleaned up | `bootstrap.py:251-264` |
| P3 | Zero relative imports across all production files | Verified globally |
| P4 | No `shell=True` anywhere -- only `create_subprocess_exec()` | Verified globally |
| P5 | DB pool: `pool_size=10`, `max_overflow=20`, `pool_pre_ping=True` | `core/db/engine.py` |
| P6 | `secrets.compare_digest()` for all API key comparisons | `app.py:92` |
| P7 | Composition root clearly documented in `orchestrator/startup.py` | `orchestrator/startup.py` |
| P8 | `shared/` has zero upward dependencies (verified) | Global import analysis |
| P9 | Context-scoped multi-tenancy with proper isolation boundaries | `core/runtime/service_factory.py` |

---

## Full Findings: Security

*Source: Opus Security Agent*

### MEDIUM

#### SEC-M1: OpenWebUI Auth Bypass Has No Production Guard
**File:** `interfaces/http/openwebui_adapter.py:52-54`

```python
if not settings.internal_api_key:
    return  # Silently skip -- no production guard
```

`app.py:66-72` blocks in production, but `openwebui_adapter.py` does not. Mitigated by `config.py:244` startup validator (app won't start in prod without key). Still a defense-in-depth gap if `environment=development` is set on a public instance.

**Fix:** Add same production guard to `verify_internal_api_key_openwebui`.

#### SEC-M2: HSTS Missing `preload` Directive; Absent on Dev-with-TLS
**File:** `interfaces/http/middleware.py:96-97`

Production HSTS: `max-age=63072000; includeSubDomains` (no `preload`). Dev instances with TLS via Traefik get no HSTS at all.

**Fix:** Add `preload` to production HSTS. Add short HSTS (`max-age=300`) for dev-with-TLS.

---

### LOW

#### SEC-L1: `datetime.utcnow()` Deprecated in JWT Creation
**File:** `core/auth/admin_session.py:42`
**Fix:** Replace with `datetime.now(UTC)`.

#### SEC-L2: Request Body in OTel Spans for Admin Portal
**File:** `interfaces/http/middleware.py:161-167`

The `skip_body` prefix list excludes `/v1/agent` and `/v1/chat/completions` but NOT `/platformadmin/`. Credential creation endpoints could have PATs/secrets captured in spans written to `spans.jsonl`.

**Fix:** Add `/platformadmin/` to `skip_body` prefix tuple.

#### SEC-L3: Crash Log Has No Rotation or Redaction
**File:** `interfaces/http/middleware.py:40-59`

Stack traces written to `data/crash.log` with no size limit, no rotation, no sensitive-value redaction.

**Fix:** Implement rotation (like `spans.jsonl`); consider sanitizing known secret patterns.

#### SEC-L4: OAuth Callback State Cleanup (Informational)
**File:** `interfaces/http/oauth.py:285`

States have 10-min TTL and are deleted after use. Correct per OAuth 2.0 spec. No action needed.

#### SEC-L5: Dynamic Rate Limits Not Enforced Per-Route
**File:** `core/middleware/rate_limit.py:24-42`

`PATH_RATE_LIMITS` dict defined but `_dynamic_limit_key` does not appear wired into slowapi enforcement. Sensitive endpoints rely only on the global 60/min default.

**Fix:** Add `@limiter.limit()` decorators to sensitive endpoints (OAuth, credentials CRUD) or verify the dynamic key is active.

---

### Positive Findings (Security) -- Highlights

| # | Finding | File:Line |
|---|---------|-----------|
| P1 | DB role is authoritative -- JWT/header claims never trusted for authorization | `admin_auth.py:148-151` |
| P2 | Production refuses to start without all critical secrets | `config.py:229-249` |
| P3 | `secrets.compare_digest()` everywhere for timing-safe comparison | `app.py:92`, `admin_api.py:75` |
| P4 | Entra ID JWKS RS256 signature verification | `admin_auth_oauth.py:90-114` |
| P5 | CSRF double-submit cookie with HMAC on all 60+ admin mutation endpoints | `csrf.py` |
| P6 | Complete security header set: CSP, HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy | `middleware.py:70-98` |
| P7 | SSRF/XSS/injection defenses: `sanitize_log()`, WIQL escaping, `html.escape()`, git URL validation | Multiple files |
| P8 | Fernet encryption at rest for all credentials/tokens | `credential_service.py:28-34` |
| P9 | PKCE (S256) for all OAuth flows | `oauth_client.py:127-141` |
| P10 | No `shell=True` in any subprocess call | Global |
| P11 | Subprocess timeouts on all external process calls | `git_clone.py:330`, `claude_code.py:390` |
| P12 | Context-isolated workspaces prevent cross-tenant file access | `git_clone.py:146-153` |
| P13 | Structured security event audit trail | `core/observability/security_logger.py` |

*Full positive findings list: P1-P33 in Security agent output.*

---

## Full Findings: Performance

*Source: Opus Performance Agent*

### HIGH

#### PERF-H1: Blocking File I/O per Request in `command_loader.py`
**File:** `core/command_loader.py:40-107`

`get_registry_index()` at `service.py:1107` calls blocking `rglob("*.md")` + YAML parse on every agentic request (~10-50ms blocked event loop per request).

**Fix:** Replace `get_registry_index()` call with `self._skill_registry.get_index()` which returns pre-loaded data from memory.

#### PERF-H2: `inspect.getouterframes()` in MemoryStore Constructor
**File:** `core/runtime/memory.py:76-79`

`inspect.getouterframes()` is called when `context_id is None` -- extremely slow (~1ms, full Python stack walk). Called per request via `ServiceFactory`.

**Fix:** Gate behind `if LOGGER.isEnabledFor(logging.WARNING)` or remove entirely.

#### PERF-H3: `SkillExecutor._validated_contexts` Cache Ineffective
**File:** `core/skills/executor.py:78`

```python
self._validated_contexts: dict[tuple[UUID, UUID], bool] = {}
```

`SkillExecutor` is created per request, so the cache never persists across requests. DB query runs every time. Cache gives false sense of optimization.

**Fix:** Move to a shared TTL cache (e.g., `functools.lru_cache` with maxsize on the underlying service, or a module-level TTL dict).

---

### MEDIUM

#### PERF-M1: `ContextManager.__init__` Calls Blocking `mkdir` Per Request
**File:** `core/context_manager.py:28-29`

Called via `ServiceFactory` per request. `Path.mkdir(parents=True, exist_ok=True)` is blocking filesystem I/O.

**Fix:** Cache `ContextManager` as a singleton (it's stateless after initialization).

#### PERF-M2: Per-Context Skill Dir Existence Check Per Request
**File:** `core/runtime/service_factory.py:162-166`

Synchronous `exists()` + `rglob("*.md")` per request even for contexts with no custom skills.

**Fix:** Cache with TTL (60s) per context_id. Use `asyncio.to_thread()` for filesystem ops.

#### PERF-M3: `ToolPermission` Query Per Request Without Caching
**File:** `core/runtime/service_factory.py:102-105`

One extra DB round-trip per request for data that changes rarely.

**Fix:** Cache per context_id with 60-300s TTL. Invalidate via admin portal change events.

#### PERF-M4: `_NoOpTraceAPI` Creates New Instance per Call
**File:** `core/observability/tracing.py:461-468`

When OTel is unavailable (tests), every `get_tracer()`, `set_span_attributes()`, etc. creates a new `_NoOpTraceAPI` instance.

**Fix:** `_NOOP_TRACE_API = _NoOpTraceAPI()` module-level singleton.

#### PERF-M5: `McpClientPool._locks` Accumulates Without Cleanup
**File:** `core/mcp/client_pool.py:46`

`defaultdict(asyncio.Lock)` -- `disconnect_context()` does clean up (`pop()`), so bounded by active contexts. No immediate action needed but worth monitoring.

#### PERF-M6: Span Export Uses `json.dumps` Instead of `orjson`
**File:** `core/observability/tracing.py:367-378`

`orjson` already used in `litellm_client.py`. Using it for span export would be 3-10x faster.

**Fix:** `orjson.dumps(record).decode()` in `_write_batch_sync()`.

---

### LOW

#### PERF-L1: `model_registry.py:76` Blocking `open()` at Singleton Init (one-time only)
#### PERF-L2: SSE streaming uses `json.dumps` per chunk (I/O bound, minor)
#### PERF-L3: `load_dotenv()` at module import time (one-time startup cost)
#### PERF-L4: Fire-and-forget `create_task()` without exception handling (`persistence.py:223`, `service.py:1018,1342`)
#### PERF-L5: `OAuthToken._fernet_cache` unbounded (bounded by key count = 1 in practice)

---

### Positive Findings (Performance) -- Highlights

| # | Finding | File:Line |
|---|---------|-----------|
| P1 | Shared `httpx.AsyncClient`: `max_connections=200`, `max_keepalive_connections=50` | `litellm_client.py:34-41` |
| P2 | Shared `AsyncQdrantClient` singleton with `_owns_client` guard | `service_factory.py:66-71` |
| P3 | DB pool: `pool_size=10`, `max_overflow=20`, `pool_recycle=3600`, `pool_pre_ping=True` | `core/db/engine.py` |
| P4 | SSE batching: 50ms interval, 10-char minimum, `asyncio.sleep(0)` yield points | `openwebui_adapter.py:376-378` |
| P5 | `get_db()` uses `async with AsyncSessionLocal()` -- sessions always returned to pool | `core/db/engine.py:22-24` |
| P6 | SkillRegistry loaded in parallel at startup via `asyncio.gather()` + `asyncio.to_thread()` | `registry.py:187-216` |
| P7 | Module-level `_verified_collections` avoids redundant Qdrant RPCs | `memory.py:32-33` |
| P8 | MCP client pool with TTL eviction (5 min) + negative caching | `client_pool.py:38-51` |
| P9 | Tool registry config loaded once with `@lru_cache` | `tools/loader.py:22-23` |
| P10 | Span export async-batched, size-rotated, background-thread | `tracing.py:213-384` |

---

## Cross-Cutting Themes

### Theme 1: Blocking I/O on the Async Event Loop
Found independently by Architecture (dispatcher direct DB sync), Performance (command_loader, mkdir, skill dir rglob). **3 medium-severity instances** of blocking work on the event loop per request. Fix: cache aggressively; use `asyncio.to_thread()` for remaining filesystem ops.

### Theme 2: Fire-and-Forget Tasks Without Error Handling
Found by both Architecture (M4) and Performance (L4). **5+ call sites** creating tasks without stored references or done callbacks. A single exception in any of these paths fails silently. Fix: implement a `tracked_task()` helper that stores refs and logs failures.

### Theme 3: Encapsulation Violations
Architecture found `service_factory.py` accessing `pool._pools` / `pool._negative_cache` (CRITICAL) and `bootstrap.py` calling `SchedulerAdapter._compute_next_run` (MEDIUM). Both bypass public APIs. Fix: add proper public methods.

### Theme 4: Logging/Telemetry Carrying Sensitive Data
Security found admin portal body captured in OTel spans (credentials). Architecture found bearer token in logs (CRITICAL). Same root cause: no systematic PII/secret filtering before writing to telemetry streams. Fix: add to `skip_body` list; remove token content from log calls.

### Theme 5: Per-Request Object Creation Overhead
Performance found `ContextManager`, `AgentService`, `SkillExecutor`, `ToolPermission` query, and per-context skill dir check all re-executed per request. These add up to potentially 50-100ms+ of unnecessary overhead on each request. Fix: cache stateless objects, add TTL caching for DB queries.

### Theme 6: Large Files Resisting Review
Architecture flagged `service.py` (1847 lines, god class) and `admin_contexts.py` (2452 lines). Both accumulate merge conflicts and are hard to test in isolation. Fix: extract sub-responsibilities into focused modules.

---

## Phased Roadmap

### Phase 1 -- Critical Fixes (Next Sprint)

1. **[ARCH-C1]** Remove bearer token content from MCP client logs (`core/mcp/client.py:209-213`)
2. **[ARCH-H1]** Sanitize user-visible exception messages in `dispatcher.py:111,122`
3. **[SEC-L2]** Add `/platformadmin/` to OTel request body skip list (`middleware.py:161-167`)
4. **[SEC-M1]** Add production guard to `verify_internal_api_key_openwebui` (`openwebui_adapter.py:52-54`)
5. **[PERF-H1]** Replace `get_registry_index()` with `self._skill_registry.get_index()` (`service.py:1107`)
6. **[PERF-H2]** Guard/remove `inspect.getouterframes()` in `memory.py:76-79`
7. **[ARCH-M4/PERF-L4]** Add `tracked_task()` helper and apply to all `create_task()` call sites

### Phase 2 -- Architecture Health (Next 2 Sprints)

8. **[ARCH-C2]** Add public API to `McpClientPool` for cached client access
9. **[ARCH-H3]** Document `shared/` layer in `ARCHITECTURE.md` with explicit rules
10. **[ARCH-H4]** Delegate dispatcher direct-DB persistence to `ConversationPersistence`
11. **[ARCH-M5]** Add DB connection timeout (`core/db/engine.py`)
12. **[PERF-M1]** Cache `ContextManager` as singleton
13. **[PERF-M3]** Cache `ToolPermission` query with TTL
14. **[PERF-M4]** Module-level `_NoOpTraceAPI` singleton
15. **[SEC-L3]** Add crash log rotation and basic sensitive-value redaction

### Phase 3 -- Refactoring (Planning Required)

16. **[ARCH-H2]** Extract `AgenticExecutor` from `AgentService` to reduce from 1847 lines
17. **[ARCH-M3]** Split `admin_contexts.py` (2452 lines) into focused modules
18. **[ARCH-H5]** Systematic `typing.Any` audit and replacement
19. **[ARCH-M2]** Replace f-string logging with `%s`-style (119 occurrences)
20. **[PERF-M2]** Cache per-context skill dir check with TTL + `asyncio.to_thread()`

### Phase 4 -- Polish & Hardening

21. **[SEC-M2]** Add `preload` to HSTS; add short HSTS for dev-with-TLS
22. **[SEC-L1]** Replace `datetime.utcnow()` with `datetime.now(UTC)`
23. **[SEC-L5]** Verify or implement per-route rate limit enforcement
24. **[PERF-M6]** Use `orjson` in span exporter
25. **[ARCH-M1]** Move `core/routing/` to `orchestrator/routing/`

---

*Generated by 4-agent parallel audit: Gemini (Domains A-M via tool reads), Opus Architecture, Opus Security, Opus Performance.*
*Full agent outputs available in task notification logs.*
