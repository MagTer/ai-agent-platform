# Comprehensive Platform Audit -- 2026-02-08

**11 parallel deep analyses completed by Opus architect agents.** Each agent independently explored the full codebase, reading source files, configs, tests, and documentation. This plan consolidates all findings into a single actionable document.

---

## Executive Summary

| Analysis Area | Critical | High | Medium | Low | Positive |
|---------------|----------|------|--------|-----|----------|
| Security | 2 | 3 | 5 | 2 | 10 |
| Architecture | 0 | 6 | 13 | 6 | 4 |
| Performance | 2 | 3 | 5 | 6 | 6 |
| Testing | 6 | 9 | 4 | 3 | -- |
| CI/CD | 1 | 3 | 4 | 2 | 5 |
| Logging/Observability | 0 | 5 | 15 | 5 | -- |
| Documentation | 3 | 1 | 8 | 7 | 8 |
| Dead Code | -- | 9 | 5 | 9+ | -- |
| Stack Script | 4 | 6 | 8 | 7 | 7 |
| Component Functionality | 0 | 5 | 12 | 5 | Many |
| Agent Config Guards | 3 | 4 | 6 | 4 | -- |
| **TOTAL** | **21** | **54** | **85** | **56** | -- |

**Overall assessment:** Strong foundation with good security practices (DB-authoritative auth, Fernet encryption, tool scoping, CSRF, header stripping). The biggest systemic risks are: (1) resource leaks from per-request object creation, (2) architecture enforcement is warn-only, (3) test coverage gaps in critical paths, and (4) missing CSP header on externally-exposed services.

---

## Top 20 Priority Fixes (Cross-Referenced)

### CRITICAL -- Fix This Week

| # | Issue | Found By | Impact | Effort |
|---|-------|----------|--------|--------|
| 1 | **No Content-Security-Policy header** | Security | XSS escalation on externally-exposed admin portal | 30 min |
| 2 | **Agent API endpoints have zero authentication** (`/v1/agent`, `/chat/completions`) | Security | Anyone on the network can use the LLM, read history | 2-4 hrs |
| 3 | **LiteLLMClient created per request in OpenWebUI adapter** -- file descriptor + memory leak | Performance, Component | Connection pool leak, growing memory | 30 min |
| 4 | **New AsyncQdrantClient per request** -- no connection reuse | Performance | ~50-200ms overhead per request, TCP churn | 2-4 hrs |
| 5 | **CI testpaths misconfigured** -- many existing tests don't run in CI | Testing | Tests exist but don't execute; false confidence | 30 min |
| 6 | **Architecture validator is warn-only** -- never blocks PRs | Agent Config | Architecture violations merge freely | 2 hrs |
| 7 | **CLAUDE.md dependency matrix contradicts architecture validator** | Agent Config, Docs | Agents produce code that violates actual rules | 30 min |

### HIGH -- Fix This Sprint

| # | Issue | Found By | Impact | Effort |
|---|-------|----------|--------|--------|
| 8 | **WebFetcher has no SSRF protection** -- can probe internal services/cloud metadata | Security, Component | Internal network enumeration | 2 hrs |
| 9 | **OAuth tokens stored in plaintext** (credentials use Fernet, tokens don't) | Security, Architecture | DB compromise exposes all OAuth tokens | 4 hrs |
| 10 | **Synchronous file I/O in DiagnosticsService** blocks event loop | Architecture | Admin portal requests block all concurrent requests | 1 hr |
| 11 | **Synchronous subprocess in GeminiCLIModel** | Architecture | Dead code -- remove entirely | 15 min |
| 12 | **HTML injection in email notification templates** | Architecture | XSS via crafted product names in price tracker emails | 1 hr |
| 13 | **CSRF module has zero tests** | Testing | Security-critical code completely untested | 2 hrs |
| 14 | **PlannerAgent and Dispatcher have zero dedicated tests** | Testing | Core orchestration path untested | 4 hrs |
| 15 | **No post-deploy health check in production** (`stack deploy`) | CI/CD, Stack | Broken deploys go unnoticed | 1 hr |
| 16 | **BuildKit disabled** (`DOCKER_BUILDKIT=0` in .env.template) | CI/CD | 30-50% slower Docker builds | 5 min |
| 17 | **No trace spans for planning/supervisor phases** | Observability | Cannot diagnose latency in critical path | 1 hr |
| 18 | **`stack down --volumes` has no confirmation for production** | Stack | One command deletes all production data | 30 min |
| 19 | **PriceTrackerTool imports from never-registered provider** -- crashes at runtime | Dead Code | Runtime crash if price tracker tool is invoked | 30 min |
| 20 | **No single-conversation diagnosis endpoint** | Observability | Cannot debug user-reported issues end-to-end | 2 hrs |

---

## A. Security Audit

### Strengths (Positive Findings)

- **DB-authoritative role checking** -- `admin_auth.py:148-168` uses `db_user.role` as sole authority, never trusts headers
- **Fernet-encrypted credential storage** -- `credential_service.py` encrypts PATs/API keys at rest
- **CSRF with HMAC-signed double-submit cookies** -- `csrf.py` uses SHA256-HMAC, constant-time comparison, SameSite=Strict
- **Traefik header stripping** -- `docker-compose.prod.yml:70-83` strips X-OpenWebUI-* headers from external requests
- **TLS 1.2+ with strong ciphers** -- `docker-compose.prod.yml:31-35` ECDHE+AES-GCM/CHACHA20, SNI strict
- **Rate limiting** -- `rate_limit.py` 60/min general, 30/min chat, 10/min admin, 5/min OAuth
- **Claude Code input sanitization** -- `claude_code.py` blocks 14 dangerous patterns, validates paths
- **Skill tool scoping** -- `executor.py:166-193` enforces frontmatter-declared tools only
- **Context ownership validation** -- `executor.py:76-121` prevents horizontal privilege escalation
- **No `shell=True` anywhere** -- all subprocess calls use `create_subprocess_exec` with explicit args

### Critical Findings

**S-CRIT-1: No Content-Security-Policy Header**
- File: `interfaces/http/app.py:141-162`
- Security headers middleware sets X-Content-Type-Options, X-XSS-Protection, Referrer-Policy, Permissions-Policy, X-Frame-Options, HSTS -- but NO CSP
- Impact: Any XSS vulnerability can execute arbitrary scripts with no restriction
- Fix: Add `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'`

**S-CRIT-2: Agent API Endpoints Have Zero Authentication**
- Files: `interfaces/http/app.py:686-754,796-824`, `openwebui_adapter.py`
- Endpoints: `/v1/agent`, `/v1/agent/chat/completions`, `/chat/completions`, `/v1/models`, `/models`, `/v1/agent/history/{conversation_id}`
- In dev: port 8001:8000 exposed directly to host with no auth
- Docker internal network: any compromised container can call agent API
- OpenWebUI identity headers not required -- falls through to anonymous
- Fix: Add shared internal API key validation, add ownership check to history endpoint

### High Findings

**S-HIGH-1: WebFetcher SSRF -- No URL Validation**
- File: `modules/fetcher/__init__.py:103-153`
- Accepts any URL, follows redirects. Can probe: `http://postgres:5432`, `http://qdrant:6333/collections`, `http://169.254.169.254/` (cloud metadata)
- Fix: Block private IP ranges, cloud metadata IPs, Docker DNS names. Whitelist http(s) schemes only.

**S-HIGH-2: OAuth Tokens Stored in Plaintext**
- File: `core/db/oauth_models.py:61-62`
- `access_token: Mapped[str]`, `refresh_token: Mapped[str | None]` -- plain strings
- Meanwhile `UserCredential.encrypted_value` uses Fernet
- Fix: Encrypt with same Fernet key, add `encrypted_access_token`/`encrypted_refresh_token` columns

**S-HIGH-3: Conversation History No Authorization**
- File: `interfaces/http/app.py:826-838`
- `/v1/agent/history/{conversation_id}` accepts any UUID, returns full history
- No ownership check -- UUID enumeration exposes any conversation
- Fix: Add user auth + context ownership check

### Medium Findings

**S-MED-1: Admin Portal XSS Risk (f-string HTML)**
- Files: `admin_credentials.py:169`, `admin_shared.py:617`
- HTML built via Python f-strings, not Jinja2 with auto-escaping
- `admin_shared.py:617`: `content` parameter injected directly into HTML without escaping
- Fix: Migrate to Jinja2 templating with auto-escaping

**S-MED-2: CORS Wildcard Methods/Headers**
- File: `interfaces/http/app.py:92-104`
- `allow_methods=["*"]`, `allow_headers=["*"]` with `allow_credentials=True`
- Fix: Restrict to specific methods and headers

**S-MED-3: Telemetry Captures Sensitive Request Bodies**
- File: `interfaces/http/app.py:198-277`
- Request body capture middleware stores up to 2000 bytes in OTel spans
- Skip list misses `/platformadmin/credentials/` and `/platformadmin/auth/`
- Fix: Add credential/auth paths to skip list

**S-MED-4: MCP Client Logs Truncated Auth Tokens**
- File: `core/mcp/client.py:214-218`
- Logs first 20 characters of Bearer token
- Fix: Remove token logging entirely

**S-MED-5: Prompt Injection via Web Content**
- Files: `core/tools/web_fetch.py`, `core/skills/executor.py:670-678`
- Web-fetched content passed directly as tool results to LLM
- Fix: Add `[EXTERNAL WEB CONTENT]` disclaimer prefix, defense-in-depth

### Low Findings

**S-LOW-1: Dev Environment Exposes All Services**
- File: `docker-compose.dev.yml`
- PostgreSQL (5433), Qdrant (6334), etc. bound to 0.0.0.0
- Fix: Bind to `127.0.0.1`

**S-LOW-2: Diagnostic Health Endpoint Unauthenticated**
- File: `admin_api.py:901-904`
- `/platformadmin/api/health` has no auth (by design as probe)
- Low risk: only returns `{"status": "ok"}`

---

## B. Architecture Analysis

### Positive Findings

- **No cross-module violations** -- modules only import within own package + core/protocols
- **Provider pattern well-implemented** -- Protocol-based DI via `core/providers.py`, wired in lifespan
- **No circular import risks** -- consistent `TYPE_CHECKING` guards
- **Path traversal protection thorough** -- `filesystem.py` and `claude_code.py` use `resolve()` + `is_relative_to()`

### High Findings

**A-HIGH-1: AgentService is a God Class**
- File: `core/core/service.py` -- 2500+ lines
- Handles: routing, conversation mgmt, plan generation, supervision, step execution, skill orchestration, memory mgmt, streaming assembly, HITL, error recovery
- Fix: Extract `ConversationManager`, `PlanOrchestrator`, `StepCoordinator`. Move to `orchestrator/`.

**A-HIGH-2: Synchronous File I/O in DiagnosticsService**
- File: `core/diagnostics/service.py:89-98`
- `with self._trace_log_path.open("r") as f: lines = deque(f, maxlen=3000)` blocks event loop
- Called from async FastAPI endpoints via admin diagnostics router
- Fix: `lines = await asyncio.to_thread(self._read_trace_lines)`

**A-HIGH-3: Synchronous subprocess.run in GeminiCLIModel**
- File: `core/models/gemini_cli.py:28`
- Uses `subprocess.run()` -- blocks event loop. File is dead code (never imported).
- Fix: Delete entire file.

**A-HIGH-4: HTML Injection in Email Templates**
- File: `modules/price_tracker/notifier.py:93-203`
- Product names, store names, URLs interpolated into HTML without `html.escape()`
- `product_url` could be `javascript:` scheme
- Fix: Use `html.escape()` for all text, validate URL schemes

**A-HIGH-5: Conversation.context_id Allows NULL in Dispatcher**
- File: `orchestrator/dispatcher.py:303-313`
- `context_id=context.id if context else None` -- but column is `Mapped[uuid.UUID]` (non-nullable)
- Would cause IntegrityError if default context doesn't exist
- Fix: Ensure default context always exists or handle missing-context explicitly

**A-HIGH-6: OAuthToken Plaintext (same as S-HIGH-2)**

### Medium Findings

**A-MED-1: `shared/` is Undocumented Fifth Layer**
- File: `services/agent/src/shared/` (3 files)
- Imported by ALL layers including core. Not tracked by architecture validator.
- `core/validators/architecture.py:111` lists only `core, modules, orchestrator, interfaces, stack`
- Fix: Add `shared` to `first_party_roots`, document its role

**A-MED-2: Architecture Validator Misses `shared/` Imports**
- File: `core/validators/architecture.py:111`
- `from shared.X import Y` treated as third-party, never validated
- Fix: Add `"shared"` to `first_party_roots`

**A-MED-3: ServiceFactory Accesses Private Attributes of McpClientPool**
- File: `core/core/service_factory.py:121-136`
- Directly accesses `pool._pools`, `pool._negative_cache`, `pool._negative_cache_ttl`
- Fix: Add public methods `get_cached_clients()`, `is_in_negative_cache()`

**A-MED-4: Dispatcher Duplicates Service Logic**
- Both Dispatcher and AgentService handle conversation resolution, message persistence, routing
- Fix: Consolidate into orchestrator layer

**A-MED-5: Mutable `default={}` in 4 SQLAlchemy Models**
- Files: `core/db/models.py:42,77,105,213`
- `Context.config`, `Conversation.conversation_metadata`, `Session.session_metadata`, `UserCredential.credential_metadata`
- Fix: Change all `default={}` to `default=dict`

**A-MED-6: Inconsistent Error Responses**
- Some admin endpoints return detailed error messages with internal paths
- Fix: Sanitize all error responses

**A-MED-7: CSRF Protection Silently Disabled Without Config**
- File: `interfaces/http/app.py:164-196`
- Only activates when `admin_jwt_secret` is configured
- Fix: Log WARNING when CSRF is disabled

**A-MED-8: Database URL Hardcoded in Engine Module**
- File: `core/db/engine.py:6-8`
- Hardcoded `postgres:postgres@postgres:5432/agent_db` default, bypasses Settings
- Fix: Accept URL from Settings class

**A-MED-9: Credential Encryption Key Defaults to Empty String**
- File: `core/core/config.py:148`
- Caught in production validator but could cause confusing errors in dev
- Fix: Generate random dev key or fail fast

**A-MED-10: Broad Exception Catching Masks Root Causes**
- `service_factory.py:140`: `except RuntimeError: pass` silently swallows MCP errors
- `dispatcher.py:241`: `except Exception` for DB persistence -- silent data loss
- Fix: Log at WARNING, add metrics

**A-MED-11: `typing.Any` Used 74 Times**
- Code standard forbids `Any`, but 74 files import it
- Key offenders: `AgentMessage.tool_calls`, `PlanStep.args`, metadata dicts
- Fix: Create `TypedDict` definitions

**A-MED-12: Orphaned Contexts Accumulate**
- File: `interfaces/http/app.py:697-741`
- `run_agent` creates new `Context(type="virtual")` per anonymous request
- No cleanup mechanism
- Fix: Add retention policy for virtual contexts

**A-MED-13: `_utc_now()` Duplicated**
- Files: `core/db/models.py:10`, `core/db/oauth_models.py:22`
- Fix: Move to shared utility

### Low Findings

- `utils/` layer not validated (only has `template.py`)
- `core/core/` nested package naming is confusing
- Late imports scattered in `app.py` lifespan (~15 late imports)
- Duplicate endpoints without deprecation mechanism (`/chat/completions` + `/v1/agent/chat/completions`)
- Missing composite index on `(platform, platform_id)` for Conversations

---

## C. Performance Analysis

### Positive Findings

- **LiteLLM httpx client** well configured: 200 max connections, 50 keepalive, configurable timeout
- **Database connection pool** well configured: pool_size=10, max_overflow=20, pool_recycle=3600, pool_pre_ping=True
- **MCP client pool** correctly implemented with TTL eviction, negative cache, double-checked locking
- **Skill registry** loaded once at startup, shared via ServiceFactory
- **Settings** cached with `@lru_cache(maxsize=1)`
- **Tool config YAML** cached with `@lru_cache(maxsize=1)`

### Critical Findings

**P-CRIT-1: LiteLLMClient Created Per Request in OpenWebUI Adapter**
- File: `interfaces/http/openwebui_adapter.py:119-122`
- `get_dispatcher()` creates new `LiteLLMClient(settings)` on every request
- Each creates new `httpx.AsyncClient` (200+50 connections configured)
- Client never closed -- `aclose()` never called
- Result: file descriptor leak, memory leak, connections in CLOSE_WAIT
- Fix: Inject shared `litellm_client` from `app.state.service_factory`

**P-CRIT-2: New AsyncQdrantClient Per Request**
- Files: `core/core/memory.py:80-98`, `core/core/service_factory.py:145-146`
- Every `ServiceFactory.create_service()` creates new `MemoryStore` -> new `AsyncQdrantClient`
- Each instantiation: new HTTP client -> new connection pool -> DNS resolution -> `get_collection()` call
- Fix: Create single shared `AsyncQdrantClient` at startup, inject into MemoryStore instances

### High Findings

**P-HIGH-1: New LiteLLMClient Per Request in Adapter (same as P-CRIT-1)**

**P-HIGH-2: AgentService Created From Scratch Per Request**
- File: `core/core/service_factory.py:63-163`
- Every request: clone tool registry + new MemoryStore + new AsyncQdrantClient + ainit()
- For rapid messages from same user, 5 requests = 5 Qdrant clients + 5 `get_collection()` calls
- Fix: TTL cache (60s) for AgentService instances keyed by context_id

**P-HIGH-3: Tool Registry Loaded Twice at Startup**
- File: `interfaces/http/app.py:380` and `core/core/service_factory.py:60`
- Both parse `tools.yaml` and dynamically import all tool classes
- `_load_tools_config` cached but `load_tool_registry()` is NOT
- Fix: Pass loaded registry from lifespan into ServiceFactory

### Medium Findings

**P-MED-1: Unbounded Validated Context Cache**
- File: `core/skills/executor.py:74`
- `self._validated_contexts: dict[UUID, UUID] = {}` -- no TTL or eviction
- Stale role data if permissions change
- Fix: Use `cachetools.TTLCache(maxsize=256, ttl=300)`

**P-MED-2: Missing Composite Index on (platform, platform_id)**
- File: `core/db/models.py:73-74`
- Dispatcher queries `WHERE platform = ? AND platform_id = ?` on every OpenWebUI request
- Neither column has an index; only `context_id` is indexed
- Fix: Add Alembic migration for composite index

**P-MED-3: Readiness Probe Creates New Qdrant Client Per Check**
- File: `interfaces/http/app.py:557-576`
- `/readyz` creates new `AsyncQdrantClient()`, calls `get_collections()`, closes it
- K8s checks every 10s = 6 new connections/minute
- Fix: Reuse shared Qdrant client

**P-MED-4: MCP Ping Uses list_tools Instead of Native Ping**
- File: `core/mcp/client.py:429-442`
- `ping()` calls `list_tools()` -- full RPC, not lightweight
- Fix: Use MCP protocol's native `ping()` method

**P-MED-5: Sequential LLM Calls (Inherent)**
- Minimum 5-8 sequential LLM calls per agentic request
- routing (1) + plan supervisor (1) + step execution (N turns) + step supervision (1) + completion (0-1)
- Fix: Consider auto-approve for steps returning `status="ok"`, merge routing+planning

### Low Findings

- Background tasks not tracked for cancellation at shutdown (`app.py:407,429`)
- No concurrency limit on MCP background connections (`service_factory.py:139`)
- Workspace cleanup is manual only (disk growth in long-running deployments)
- Crash log (`data/crash.log`) grows unbounded -- no rotation
- Skill executor message history grows during execution (no truncation of old tool outputs)
- Retention trim is O(N) per conversation (acceptable, runs daily)

---

## D. Testing Gap Analysis

**Estimated coverage: ~35%** across ~620 test functions in ~60 files.

### P0 -- Fix Immediately

**T-P0-1: CSRF Module Has Zero Tests**
- File: `interfaces/http/csrf.py` (180 lines)
- Security-critical: token generation, HMAC validation, double-submit pattern
- Needed tests: generation format, valid acceptance, tamper rejection, timing attack resistance, cookie/header mismatch rejection

**T-P0-2: CI testpaths Misconfigured**
- File: `pyproject.toml` testpaths
- Current: `["../../tests", "src/core/tests", "src/stack/tests"]`
- Missing: `tests/unit/`, `tests/core/`, `tests/interfaces/`, `src/interfaces/http/tests/`, `src/modules/*/tests/`, `src/shared/tests/`, `src/utils/tests/`
- Many existing tests likely never run in CI

**T-P0-3: PlannerAgent Has Zero Dedicated Tests**
- File: `core/agents/planner.py` (464 lines)
- JSON extraction, retry logic (3 attempts), conversational detection, sanitization -- all untested
- `_extract_json_fragment` static method untested
- `_is_conversational_message` fallback untested

**T-P0-4: Dispatcher Has Zero Tests**
- File: `orchestrator/dispatcher.py` (446 lines)
- Slash command parsing, fast path matching, orchestration delegation untested

**T-P0-5: claude_code.py Has Zero Tests**
- File: `core/tools/claude_code.py`
- Executes subprocesses from user input with 14 DANGEROUS_PATTERNS -- none tested individually

**T-P0-6: Admin Portal Auth Bypass Tests Missing**
- All `admin_*.py` routes need negative auth tests (access without valid session/headers)

### P1 -- Fix Soon

- **AgentService** (2514 lines, 3 tests) -- streaming, replan loop, supervisor integration untested
- **Retention cleanup** (`retention.py`) -- complex SQL with subqueries, zero DB tests
- **MCP tool wrapper** (`McpToolWrapper`) -- bridges remote/local interface, untested
- **OAuth end-to-end** -- token lifecycle (create, refresh, revoke, inject) untested
- **Tool loader validation** -- tools.yaml class references untested
- **Git clone path traversal** -- `repo_url` parameter validation untested
- **Web fetch SSRF** -- URL validation for internal addresses untested
- **Diagnostic API auth** -- `X-API-Key` validation for all endpoints untested
- **Response agent** (`response_agent.py`) -- zero coverage

### P2 -- Next Sprint

- Coverage reporting in CI (pytest `--cov`)
- Flaky test detection (`pytest-rerunfailures`)
- Contract tests for external APIs (VCR/snapshot)
- Performance benchmarks (startup, streaming latency)
- Test data factories
- Alembic migration up/down tests

### Test Infrastructure Issues

- **Fixture duplication**: `mock_litellm` defined in global conftest AND individual test files
- **No shared test data factories**: each test creates its own PlanStep/AgentRequest objects
- **`test_service.py` uses deeply nested MagicMock chains** instead of SQLite fixture
- **MockLLMClient lacks error simulation**: no way to test partial failures, rate limits, malformed JSON

---

## E. CI/CD Pipeline Analysis

### Positive Findings

- Parallel CI jobs (lint, typecheck, test via pytest-xdist)
- Quality gate pattern with `if: always()` aggregation
- CodeQL SAST with `security-extended` queries
- Non-root container user
- `.dockerignore` excludes tests, caches, `.env`

### Critical Finding

**CI-CRIT-1: BuildKit Disabled**
- File: `.env.template` -- `DOCKER_BUILDKIT=0`, `COMPOSE_DOCKER_CLI_BUILD=0`
- Disables: parallel layer builds, cache mounts, advanced caching
- Fix: Set to `1` or remove (BuildKit is default in Docker 23.0+)
- Impact: 30-50% faster builds

### High Findings

**CI-HIGH-1: No Push-to-Main Trigger**
- CI only on `pull_request`, not `push` to main
- Post-merge regressions go undetected
- Fix: Add `push: branches: [main]`

**CI-HIGH-2: No Dependency Vulnerability Scanning**
- No Dependabot, no `pip-audit`, no Snyk
- 50+ Python dependencies with many transitive deps
- Fix: Add `.github/dependabot.yml` for pip + github-actions

**CI-HIGH-3: No Container Image Scanning**
- Built agent image never scanned
- Fix: Add Trivy container scan job

### Medium Findings

- Architecture validation not in CI pipeline
- `data/` (54MB) not in `.dockerignore` -- inflates build context transfer
- CI duplicates check logic (raw `python -m ruff/black/mypy/pytest`) instead of `./stack check`
- No coverage reporting in CI
- Third-party images use `:latest` instead of digest pins
- Production deploy has no post-deploy health verification
- Single-depth rollback only (one "previous" image)
- No Slack/Teams notification on CI failure

---

## F. Logging & Observability Gap Analysis

### Current Architecture

The platform has **comprehensive observability built on OpenTelemetry**:

```
Requests --> FastAPI Middleware --> OTel Spans --> _FileSpanExporter --> data/spans.jsonl
                                       |
                                       +--> OTLP Exporter (if OTEL_EXPORTER_OTLP_ENDPOINT set)
                                       +--> LiteLLM auto-instrumentation

Debug Events --> DebugLogger --> PostgreSQL debug_logs table (24h retention)
Security Events --> SecurityLogger --> data/system_events.jsonl
App Logs --> JSON Formatter --> data/app_logs.jsonl (WARNING+)
```

**Diagnostic API** (`/platformadmin/api/`) reads from these sources and exposes aggregated metrics. **Admin dashboard** visualizes traces with waterfall view, metrics cards, component health.

### High Findings

**O-HIGH-1: No Trace Span for Planning Phase**
- File: `core/core/service.py` -- `_generate_plan` method
- Plan generation (2-10s LLM call) has no dedicated span
- Cannot measure planning latency separately from step execution
- Fix: `with start_span("agent.planning", attributes={"replan_count": N}):`

**O-HIGH-2: No Trace Span for Supervisor Evaluation**
- File: `core/core/service.py:598`
- `step_supervisor.review()` invokes LLM (1-5s), no span
- Invisible in trace waterfall
- Fix: `with start_span("agent.supervisor.review", attributes={"step": label}):`

**O-HIGH-3: No Single-Conversation Diagnosis Endpoint**
- Given a `conversation_id`, no endpoint returns: all debug events + all trace spans + routing decision + token usage + outcome
- Currently requires 3 separate API calls with manual correlation
- Fix: Add `GET /platformadmin/api/conversations/{id}/diagnosis`

**O-HIGH-4: Security Events Not Queryable via API**
- File: `core/observability/security_logger.py`
- Events written to `data/system_events.jsonl` -- require SSH to query
- Fix: Add `GET /platformadmin/api/security/events?event_type=AUTH_FAILURE&hours=24`

**O-HIGH-5: No Latency Percentile Tracking**
- API returns average and max duration but no p50/p95/p99
- Average masks tail latency
- Fix: Compute percentiles in spans analysis, or expose via histograms

### Medium Findings (15)

- Debug logging gated behind toggle -- no always-on request summaries
- LLM cost not stored in database (only in span attributes in JSONL)
- No debug log query by conversation_id API endpoint
- No MCP server status endpoint with per-server detail
- Error codes not attached to trace spans (structured error data missing)
- Stack traces not in debug logs (only `f"Error: {e}"`, no traceback)
- No alert condition detection (no background health monitor)
- Admin actions not fully audited (config changes, deletions not logged)
- OAuth token usage not tracked (which tools used which tokens)
- Plan execution history lost after 24h retention
- No routing decision reasoning logged
- No plan supervisor review logged separately
- No span for chat route or completion generation
- No trace sampling configuration (all spans exported)
- MCP connection/disconnection events not logged as structured events

### Prometheus Analysis -- Where It Fits

**Current state:** The platform has NO Prometheus, Grafana, or external time-series monitoring. All metrics are computed on-demand by reading `data/spans.jsonl` and the `debug_logs` DB table when API endpoints are called. There is no `/metrics` endpoint (though it is already in the body-capture skip list at `app.py:218`, suggesting it was planned).

**What already exists that Prometheus would replace/augment:**

| Capability | Current Implementation | Prometheus Equivalent |
|-----------|----------------------|----------------------|
| Error rate | DiagnosticsService reads last 3000 lines of spans.jsonl on each API call | `agent_requests_total{status}` counter -- instant, no file parsing |
| Request latency | Computed from spans.jsonl per-endpoint on demand | `agent_request_duration_seconds` histogram with percentiles |
| Component health | Integration tests run on demand via `/diagnostics/run` | `agent_component_health{component}` gauge updated by background task |
| Tool execution stats | Aggregated from debug_logs DB table | `agent_tool_calls_total{tool,status}` counter |
| Skill outcomes | Aggregated from debug_logs DB table | `agent_skill_executions_total{skill,outcome}` counter |
| Token usage | Only in span attributes, not queryable | `agent_llm_tokens_total{model,type}` counter |
| Active connections | Not tracked | `agent_mcp_connections{server,status}` gauge |

**Assessment: Prometheus is LOWER priority than originally suggested.** Here's why:

1. **The admin portal already provides good observability** via the DiagnosticsService + diagnostic API. The data is there -- it is just read from files/DB on demand rather than pre-aggregated in a time-series DB.

2. **The primary gaps are in data capture, not data exposure.** Missing trace spans (planning, supervisor), missing per-conversation diagnosis, and debug logging behind a toggle are bigger problems than the format in which metrics are exposed.

3. **Adding Prometheus requires infrastructure changes** (Prometheus server + Grafana in docker-compose, ~200MB+ memory), whereas the higher-priority observability fixes (trace spans, diagnosis endpoint, security event API) are pure application code changes.

4. **Prometheus becomes valuable when:**
   - You need alerting (error rate > 10% for 5 min -> PagerDuty/Slack)
   - You need long-term trend analysis (latency degradation over weeks)
   - You need real-time dashboards beyond the admin portal
   - You scale to multiple agent instances (Prometheus scrapes all replicas)

**Revised priority: Phase 4 (not Phase 3).** Fix the data capture gaps first (trace spans, diagnosis endpoint, always-on logging), then add Prometheus as the exposure layer when alerting/dashboards become needed.

---

## G. Documentation Analysis

### Positive Findings

- **CLAUDE.md** is remarkably comprehensive (720+ lines) -- effective AI assistant entry point
- **API_REFERENCE.md** thorough at 2004 lines with examples for every endpoint
- **Code docstrings** in recent modules follow Google-style with Args/Returns/Raises
- **`.env.template`** well-organized with generation commands
- **config.py Settings** has Pydantic Field descriptions for self-documentation
- **Skill files** well-written with workflow phases and output format specs
- **`docs/` directory** has 30+ documentation files
- **Security logging** well-documented in code

### High Findings

**D-HIGH-1: Architecture Docs Reference SQLite (System Uses PostgreSQL)**
- File: `docs/architecture/02_agent.md`
- References `agent.core.state` (SQLite persistence module), `StateStore (SQLite)`
- Actual: PostgreSQL via SQLAlchemy (`core.db.engine`, `core.db.models`)
- Module paths use `agent.core.X` but actual is `core.core.X`

**D-HIGH-2: Tools Doc Lists 1 of 14+ Tools**
- File: `docs/architecture/03_tools.md`
- "Registered tools" table lists only `web_fetch`. Actual `config/tools.yaml` has 14+.
- References `executor: "remote"` which doesn't exist
- Test path `src/agent/tests/test_tools.py` is wrong

**D-HIGH-3: ARCHITECTURE.md Has Wrong Protocol Names**
- File: `docs/ARCHITECTURE.md:~252-290`
- Lists `EmbedderProtocol`, `MemoryProtocol`, `LLMProtocol`, `ToolProtocol`
- Actual: `IEmbedder`, `IFetcher`, `IRAGManager`, `ICodeIndexer`, `IOAuthClient`, `IPriceTracker`, `IEmailService`
- DI location says `core/core/app.py` but actual is `interfaces/http/app.py`

**D-HIGH-4: No Production Deployment Guide**
- Missing `docs/DEPLOYMENT.md`
- No SSL/TLS setup documentation, no backup/restore procedures
- `./stack deploy` mentioned in CLAUDE.md but not documented in docs/

### Medium Findings

- CLAUDE.md admin section uses non-existent `admin_page_layout`, `NAV_ITEMS` (actual: `ADMIN_NAV_ITEMS`, 13 items)
- CLAUDE.md lists non-existent protocols (ILLMProtocol, MemoryProtocol, ToolProtocol)
- `SKILLS_FORMAT.md` schema (`inputs`, `permission`) doesn't match actual frontmatter
- `ROADMAP.md` frozen at early milestone -- doesn't mention multi-tenancy, admin portal, MCP, OAuth, skills
- 5+ tool classes exist but not in tools.yaml -- unclear if deprecated or planned
- README.md minimal (43 lines), uses wrong CLI invocation (`python -m stack up` vs `./stack up`)
- ARCHITECTURE.md admin API endpoints use `/admin/` prefix (actual: `/platformadmin/`)
- OPERATIONS.md references non-existent `scripts/stack_tools.py` and `Stack-Health.ps1`
- `getting_started.md` lists only 7 env vars (actual: 40+)
- `.env.template` has `AGENT_SQLITE_STATE_PATH` (likely unused)
- n8n references in CLI and docs for removed feature

---

## H. Dead Code Analysis

### High-Confidence Removals (Safe to Delete)

**Unregistered Tool Implementations:**

| File | Tool Name | Reason |
|------|-----------|--------|
| `core/tools/qa.py` | `run_pytest`, `run_linter` | Superseded by `test_runner.py`, never registered |
| `core/tools/search_code.py` | `search_codebase` | Never registered in any tools.yaml |
| `core/tools/oauth_authorize.py` | `oauth_authorize` | Never imported anywhere |
| `core/tools/github.py` | `github_repo` | Registered in WRONG tools.yaml (root, not services/agent) |
| `core/tools/filesystem.py` | `list_directory`, `edit_file` | Defined but only `read_file` registered. Used only in tests. |

**Unused Modules:**

| Path | Reason |
|------|--------|
| `modules/context7/` (3 files) | Superseded by MCP client pool (`client_pool.py:151-157`) |
| `core/models/gemini_cli.py` | Never imported. Uses synchronous `subprocess.run` (violates async-first). |
| `interfaces/protocols.py` | `IPlatformAdapter`, `IAssistantClient` never imported. Base.py `PlatformAdapter` used instead. |
| `core/protocols/oauth.py` | `IOAuthClient` protocol never imported outside package. TokenManager has own interface. |

**Orphaned Scripts:**

| File | Reason |
|------|--------|
| `src/check_qdrant_api.py` | Standalone debug script |
| `src/manual_search.py` | Standalone debug script with hardcoded paths |
| `tests/phase5_demo.py` | References deleted tools (`index_codebase`, `search_codebase`) |
| `scripts/test_unified_orchestrator.py` | Ad-hoc script, not pytest |

**Duplicate/Conflicting Config:**
- Root `config/tools.yaml` (4 tools) -- app loads from `services/agent/config/tools.yaml` (15 tools). Root file is unused.

**Unused Dependencies (pyproject.toml):**
- `respx` -- HTTP mock library, never imported
- `aiosqlite` -- async SQLite driver, system uses PostgreSQL

**Deprecated Config Settings:**
- `sqlite_state_path` in Settings -- system uses PostgreSQL
- `price_tracker_from_email` -- explicitly marked DEPRECATED with backwards-compat validator

### Runtime Bug

**DC-BUG-1: PriceTrackerTool Imports from Never-Registered Provider**
- File: `core/tools/price_tracker.py`
- Imports `get_price_tracker` from `core.providers` -- but `set_price_tracker()` is NEVER called
- Would raise `ProviderError` at runtime if invoked
- The `modules/price_tracker/__init__.py` has its OWN `get_price_tracker()` -- different function
- Fix: Fix the import or remove the tool's provider dependency

### Medium-Confidence (Likely Dead)

- `core/routing/intent.py` `IntentClassifier` -- superseded by UnifiedOrchestrator (docstring confirms)
- `core/routing/guidance.py` -- routing guidance constants, not imported by production code
- `interfaces/telegram/adapter.py` -- complete implementation but never started or registered in app.py
- `interfaces/base.py` `PlatformAdapter` -- only used by TelegramAdapter + OpenWebUIAdapter
- Provider functions `set_price_tracker`/`get_price_tracker` in `core/providers.py` -- never called

### Tools Registered but Used by Zero Skills

| Tool | In tools.yaml | Referenced by Skill |
|------|--------------|-------------------|
| `clock` | Yes | No |
| `calculator` | Yes | No |
| `test_runner` | Yes | No |
| `send_email` | Yes | No |

These may still be usable by the executor directly (not via skills). Needs further analysis before removal.

---

## I. Stack Script Analysis

### Strengths

- Self-bootstrapping entry point (auto-detects virtualenv, re-executes via Poetry)
- Dev/Prod isolation (separate project names, compose files, ports)
- Deployment history (`.stack/deployments.json` with 10-entry cap)
- Image tagging for rollback (current -> "previous" before build)
- Typer + Rich framework -- auto-generated help, good formatting
- 32 commands across 8 groups
- Good test coverage for core check paths

### Critical Findings

**ST-CRIT-1: `Any` Type in `run_git_command`**
- File: `stack/tooling.py:91`
- `**kwargs: Any` pass-through bypasses type safety
- Fix: Replace with explicit parameters

**ST-CRIT-2: Duplicate SearxNG Health Check in `up`**
- File: `stack/cli.py:307-358`
- SearxNG checked in `service_checks` loop (60s timeout) then again in "HTTP frontends" (30s timeout)
- Fix: Remove duplicate, move Open WebUI into first list

**ST-CRIT-3: CI Does Not Use Stack CLI**
- File: `.github/workflows/ci.yml`
- CI runs raw `python -m ruff/black/mypy/pytest` instead of `./stack check --no-fix`
- Black's `--extend-exclude` duplicated between CI and checks.py
- Architecture validation runs locally but not in CI
- Fix: Refactor CI to use `./stack lint --no-fix`, `./stack typecheck`, `./stack test`

**ST-CRIT-4: `db rollback` UX Issues**
- File: `stack/cli.py:1518-1563`
- Only supports single-step rollback
- Uses `input()` with Rich markup that renders as literal text
- Fix: Add `--revision`/`--steps` options, use `typer.confirm()`

### High Findings

**ST-HIGH-1: No Timeout on Any Subprocess Call**
- File: `stack/tooling.py:63`
- Every `run_command()` can hang indefinitely
- Fix: Add `timeout` parameter with sensible defaults (300s builds, 60s health, 30s status)

**ST-HIGH-2: `repo save` Uses `git add -A`**
- File: `stack/tooling.py:284`
- Stages ALL files including `.env`, credentials, keys
- Fix: Add pre-commit sensitive file scan or prompt before staging

**ST-HIGH-3: `.env` Values Override OS Environment**
- File: `stack/utils.py:24-38`
- Inverted from standard convention (OS should take precedence)
- Fix: Document prominently or invert priority

**ST-HIGH-4: No Confirmation for `stack down --volumes`**
- File: `stack/cli.py:365-395`
- In production mode, deletes all persistent data with zero warning
- Fix: Add `typer.confirm()` when `remove_volumes=True` and `prod=True`

**ST-HIGH-5: `qdrant restore` Deletes Before Validating**
- File: `stack/qdrant.py:130`
- `rm -rf /qdrant/storage/*` before extracting backup
- Corrupt backup = total data loss
- Fix: Rename existing storage, validate tar, then cleanup

**ST-HIGH-6: `_connect_traefik_to_dev_network` Uses Raw subprocess.run**
- File: `stack/cli.py:428-448`
- Bypasses centralized `run_command()` error handling
- Fix: Use `tooling.run_command()`

### Medium Findings (8)

- Inconsistent output mechanisms (Rich vs ANSI codes vs typer.echo)
- `cli.py` is 1600 lines -- should split into modules
- Hardcoded `skip_architecture=True` in deploy/publish
- Production deploy has no post-deploy health verification
- `--prod` and `--dev` flags not mutually exclusive
- No `stack version` command
- `ensure_secrets` only checks 2 of many required secrets
- Qdrant backup uses naive `datetime.now()` without timezone

---

## J. Component Functionality Analysis

### Orchestrator/Planner -- Maturity: HIGH

**Strengths:** Complete adaptive execution loop, 3 replans, exponential backoff, auto-replan pattern detection, parallel step grouping with cycle detection, HITL with state persistence, deprecated step migration.

**Gaps:**
- **UnifiedOrchestrator JSON detection fragile** -- brace-matching confuses code examples with plans
- **Planner input truncated at 4000 chars silently** -- long messages lose context without warning
- **Plan supervisor doesn't block on warnings** -- unknown tools execute anyway
- **`_generate_completion` creates orphan conversation_id** for debug logging

### Skill System -- Maturity: HIGH

**Strengths:** Startup validation, async parallel loading, multi-index lookup, strict tool scoping, context ownership validation, HITL support, tool deduplication and rate limiting, streaming with activity hints.

**Gaps:**
- **Context injection hardcoded for specific tools** -- `executor.py:643-647` only injects for homey/azure_devops, not git_clone/claude_code/github_pr in skills-native path
- **Tool rate limit (max 3) not configurable per tool** -- research skills hit limit quickly
- **No overall skill execution timeout** -- individual tools have 120s, but entire skill loop has none
- **HITL state in JSONB column** -- could grow large for long conversations

### Tool System -- Maturity: MIXED

| Tool | Maturity | Key Gap |
|------|----------|---------|
| `homey.py` | HIGH | Creates new httpx.AsyncClient per API call (no reuse) |
| `azure_devops.py` | HIGH | Uses synchronous `msrest.Connection` (blocks event loop) |
| `git_clone.py` | HIGH | No URL validation; `reset --hard` fallback discards changes silently |
| `claude_code.py` | EXCELLENT | No significant gaps found |
| `github_pr.py` | MEDIUM | `git add -A` stages sensitive files; no branch name validation |
| `web_search.py` | MEDIUM | No issues found |
| `web_fetch.py` | MEDIUM | No size limit on fetched content; SSRF (see Security) |
| `send_email.py` | HIGH | Self-only mode, good validation, HTML sanitization |
| `calculator.py` | EXCELLENT | AST-based safe eval, no eval() |
| `test_runner.py` | MEDIUM | No symlink protection; user-controlled args to pytest |
| `mcp_loader.py` | HIGH | Clean wrapper, auth error detection, name prefixing |
| `price_tracker.py` | MEDIUM | Uses `Any` type, no per-action error handling, broken provider import |

### LiteLLM Client -- Maturity: HIGH

**Strengths:** 200/50 connection pool, streaming SSE with reasoning model support, raw token stripping, TTFT/latency metrics, usage tracking, model capability registry.

**Gaps:**
- No retry on transient HTTP failures (502/503)
- Stream parsing assumes SSE format
- `generate()` falls back to thinking content for reasoning models (could expose internal reasoning)

### RAG Module -- Maturity: MEDIUM

**Gaps:**
- **Naive 1000-char chunking** -- splits mid-sentence/word, poor retrieval quality
- No metadata filtering beyond collection_name
- No re-ranking step (cross-encoder)
- Hardcoded 4096 vector dimensions

### MCP Client -- Maturity: HIGH

**Strengths:** Transport auto-detection (Streamable HTTP + SSE fallback), exponential backoff retry, per-context pool, negative caching, OAuth token support.

**Gaps:**
- Auth token logged (truncated to 20 chars -- still a security concern)
- Pool pings every cached client on every request (adds latency)
- `service_factory.py` accesses private attributes of pool

### Conversation Management -- Maturity: HIGH

**Gaps:**
- No conversation history limit (loads ALL messages for a session)
- No message pruning or summarization
- Context creation race condition in concurrent requests
- `_inject_pinned_files` allows reading from user's home directory broadly

### SSE Streaming -- Maturity: HIGH

**Strengths:** OpenAI-compatible format, 50ms token batching, content filtering by verbosity level, 14 raw model tokens stripped, noise fragment detection, 12 event types.

**Gaps:**
- No backpressure mechanism (server queues if client is slow)
- No SSE keepalive/heartbeat (proxies may close idle connections)
- Content classifier uses hardcoded token list (needs manual update for new models)

---

## K. Agent Config Architecture Guards

### Critical Findings

**AG-CRIT-1: Architecture Validator is Warn-Only**
- File: `stack/checks.py:126-133`
- Returns `success=True` even with violations
- Deploy and publish ALSO skip: `skip_architecture=True`
- Fix: Add `.architecture-baseline.json` for known violations, fail on NEW violations

**AG-CRIT-2: CLAUDE.md Dependency Matrix Contradicts Validator**
- CLAUDE.md: `interfaces -> modules` ALLOWED
- Validator (`architecture.py:199-222`): `interfaces -> modules` FLAGGED
- The validator is architecturally correct
- Fix: Update CLAUDE.md matrix to disallow `interfaces -> modules`

**AG-CRIT-3: Architecture Check Not in CI**
- `.github/workflows/ci.yml` has lint, typecheck, test -- no architecture
- Fix: Add architecture validation job to CI

### High Findings

**AG-HIGH-1: Module Upward Imports Not Checked**
- Validator only checks cross-module imports
- Modules importing from `orchestrator/` or `interfaces/` would not be caught
- Fix: Add upward import check in `_check_module_imports`

**AG-HIGH-2: Orchestrator Layer Has Zero Validation**
- No checks for orchestrator importing interfaces
- Fix: Add `_check_orchestrator_imports` method

**AG-HIGH-3: TYPE_CHECKING Imports Cause False Positives**
- Validator doesn't distinguish runtime vs TYPE_CHECKING imports
- `TYPE_CHECKING` imports from higher layers are acceptable (type-only)
- Fix: Parse AST for `if TYPE_CHECKING:` blocks, skip those imports

**AG-HIGH-4: `.clinerules` References Deleted Files**
- References `.claude/PRIMER.md` (does not exist)
- Uses `/architect`, `/builder`, `/janitor` (actual: `/plan`, `/build`, `/ops`)
- Fix: Update references

### Medium Findings

- `shared/` layer has no validation rules
- No relative import detection
- Deploy/publish hardcode `skip_architecture=True`
- No pre-commit architecture hook
- `shared/` undocumented in CLAUDE.md
- No anti-patterns section documented
- Agent instructions test location inconsistency (`src/core/tests/` vs `tests/unit/`)
- Ops.md claims `stack check` includes architecture validation (misleading -- it's warn-only)
- PLAN_TEMPLATE.md references `Opus 4.5` (should be `Opus 4.6`)
- Emoji in ops.md PR template (violates no-emojis rule)

---

## Additional Perspectives (Cross-Cutting Themes)

### 1. Provider Pattern Inconsistency

Some tools use `core/providers.py` DI, others create own instances. The price tracker has a BROKEN provider that would crash at runtime. The Homey tool, Azure DevOps tool, and WebFetcher module all create new HTTP clients per call instead of using pooled connections.

### 2. Timezone Handling

Mixed usage of `datetime.utcnow()` (naive) and `datetime.now(UTC)` (aware) throughout DB models, qdrant backup, and logging. Python's `utcnow()` is a known anti-pattern. Should standardize on `datetime.now(UTC)` with timezone-aware columns.

### 3. Connection Lifecycle

| Component | Creates Client | Reuses | Properly Closed |
|-----------|---------------|--------|-----------------|
| LiteLLMClient (main) | Once at startup | Yes | Yes (aclose in shutdown) |
| LiteLLMClient (adapter) | Per request | No | No (leak!) |
| AsyncQdrantClient | Per request | No | No |
| Homey httpx.AsyncClient | Per API call | No | Unknown |
| Azure DevOps Connection | Per tool call | No | Synchronous |
| DB engine | Once at startup | Yes | Yes (pool) |
| MCP clients | Per context (cached) | Yes | Yes (pool eviction) |

### 4. Language Consistency

Price tracker has Swedish-language strings mixed with English code, despite the rule that only user-facing chat messages should be in Swedish. All code, web content, UI text, config, and comments should be English.

### 5. Error Recovery Asymmetry

- **Planner**: retry logic (3 attempts) for JSON parse failures
- **Skill executor**: retry via supervisor (RETRY outcome)
- **Dispatcher**: NO retry logic -- transient failure = entire request fails
- **LiteLLM client**: NO retry on 502/503 from proxy

---

## Consolidated Roadmap

### Phase 1: Critical Security & Stability (1-2 days)

| # | Task | Files | Effort |
|---|------|-------|--------|
| 1 | Add CSP header to security middleware | `app.py` | 30 min |
| 2 | Add internal API key auth to agent endpoints | `app.py`, `openwebui_adapter.py` | 2-4 hrs |
| 3 | Fix LiteLLMClient leak (reuse shared client) | `openwebui_adapter.py` | 30 min |
| 4 | Fix CI testpaths | `pyproject.toml` | 30 min |
| 5 | Fix CLAUDE.md dependency matrix | `CLAUDE.md`, `architect.md` | 30 min |
| 6 | Remove safe dead code batch 1 | `gemini_cli.py`, `qa.py`, `search_code.py`, `oauth_authorize.py`, `protocols.py`, debug scripts | 1 hr |
| 7 | Enable BuildKit | `.env.template` | 5 min |
| 8 | Add `data/` to `.dockerignore` | `services/agent/.dockerignore` | 5 min |
| 9 | Fix PriceTrackerTool provider import | `core/tools/price_tracker.py` | 30 min |

### Phase 2: High-Priority Fixes (3-5 days)

| # | Task | Files | Effort |
|---|------|-------|--------|
| 10 | Share AsyncQdrantClient across requests | `memory.py`, `service_factory.py`, `app.py` | 4 hrs |
| 11 | Add SSRF protection to WebFetcher | `modules/fetcher/__init__.py` | 2 hrs |
| 12 | Encrypt OAuth tokens at rest | `oauth_models.py`, migration | 4 hrs |
| 13 | Fix sync I/O in DiagnosticsService | `core/diagnostics/service.py` | 1 hr |
| 14 | Fix HTML injection in email templates | `modules/price_tracker/notifier.py` | 1 hr |
| 15 | Write CSRF tests | New: `tests/unit/test_csrf.py` | 2 hrs |
| 16 | Write PlannerAgent/Dispatcher tests | New test files | 4 hrs |
| 17 | Add architecture check to CI | `.github/workflows/ci.yml` | 1 hr |
| 18 | Make validator fail on new violations (baseline) | `stack/checks.py` | 2 hrs |
| 19 | Add post-deploy health check to `stack deploy` | `stack/cli.py` | 1 hr |
| 20 | Add confirmation for `stack down --volumes --prod` | `stack/cli.py` | 30 min |
| 21 | Fix mutable `default={}` in SQLAlchemy models | `core/db/models.py` | 30 min |
| 22 | Add `shared` to architecture validator roots | `core/validators/architecture.py` | 30 min |

### Phase 3: Testing & Observability (1-2 weeks)

| # | Task | Files | Effort |
|---|------|-------|--------|
| 23 | Add trace spans for planning/supervisor | `core/core/service.py` | 1 hr |
| 24 | Add per-conversation diagnosis endpoint | `admin_api.py` | 2 hrs |
| 25 | Add security events query endpoint | `admin_api.py` | 2 hrs |
| 26 | Write claude_code.py pattern tests | New test file | 2 hrs |
| 27 | Write admin portal auth bypass tests | Extend test files | 2 hrs |
| 28 | Add Dependabot configuration | `.github/dependabot.yml` | 30 min |
| 29 | Add coverage reporting to CI | `.github/workflows/ci.yml`, `pyproject.toml` | 1 hr |
| 30 | Extend validator (modules upward, TYPE_CHECKING, orchestrator) | `architecture.py` | 4 hrs |
| 31 | Add push-to-main CI trigger | `.github/workflows/ci.yml` | 15 min |
| 32 | Refactor CI to use `./stack` commands | `.github/workflows/ci.yml` | 2 hrs |
| 33 | Add always-on request summary logging | `core/debug/logger.py`, `core/core/service.py` | 2 hrs |
| 34 | Remove dead code batch 2 | `context7/`, root `config/tools.yaml`, unused deps | 1 hr |

### Phase 4: Architecture & Performance (2-4 weeks)

| # | Task | Files | Effort |
|---|------|-------|--------|
| 35 | Cache AgentService per context_id with TTL | `service_factory.py` | 4 hrs |
| 36 | Add DB index on (platform, platform_id) | Migration | 1 hr |
| 37 | Begin AgentService decomposition | `service.py` -> new modules | 2-3 days |
| 38 | Split `cli.py` into focused modules | `stack/cli.py` -> modules | 4 hrs |
| 39 | Migrate admin portal to Jinja2 | `admin_*.py`, new templates | 2-3 days |
| 40 | Update stale architecture docs | `02_agent.md`, `03_tools.md`, `ARCHITECTURE.md` | 4 hrs |
| 41 | Add conversation history limits + summarization | `service.py` | 4 hrs |
| 42 | Add SSE keepalive heartbeat | `openwebui_adapter.py` | 2 hrs |
| 43 | Add anti-patterns section to CLAUDE.md | `CLAUDE.md` | 1 hr |
| 44 | Add Prometheus metrics endpoint | New: `observability/metrics.py`, `app.py` | 4 hrs |
| 45 | Fix .clinerules outdated references | `.clinerules` | 30 min |
| 46 | Remove remaining dead code | telegram, scripts, unused __init__ exports | 2 hrs |

### Phase 5: Polish & Hardening (ongoing)

| # | Task | Effort |
|---|------|--------|
| 47 | Contract tests for OpenAI API compatibility | 4 hrs |
| 48 | Chaos/fault injection tests | 4 hrs |
| 49 | Performance benchmarks (startup, streaming) | 4 hrs |
| 50 | Improve RAG chunking (sentence-aware) | 4 hrs |
| 51 | Reduce `Any` usage with TypedDict | 4 hrs |
| 52 | Add `/review` and `/audit` slash commands | 2 hrs |
| 53 | Pin third-party Docker images by digest | 1 hr |
| 54 | Add Trivy container scanning to CI | 2 hrs |
| 55 | Create production deployment guide | 4 hrs |
| 56 | Create backup/restore documentation | 2 hrs |
| 57 | Add subprocess timeout to `run_command()` | 2 hrs |
| 58 | Fix `.env` precedence (OS should override) | 1 hr |
| 59 | Standardize timezone handling (datetime.now(UTC)) | 2 hrs |
| 60 | Add `/review` command for architecture review | 1 hr |
