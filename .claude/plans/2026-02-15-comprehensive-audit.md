# Comprehensive Platform Audit

**Date:** 2026-02-15
**Auditors:** 16 parallel architect agents (3 Opus, 13 Sonnet)
**Scope:** Full AI Agent Platform codebase

---

## 1. Executive Summary

| Area | CRITICAL | HIGH | MEDIUM | LOW | Positive |
|------|----------|------|--------|-----|----------|
| A. Architecture | 0 | 1 | 3 | 2 | 6 |
| B. Documentation | 0 | 1 | 2 | 3 | 5 |
| C. Security | 0 | 3 | 5 | 2 | 14 |
| D. Component Functionality | 1 | 3 | 4 | 2 | 12 |
| E. Dead Code | 0 | 0 | 1 | 3 | 5 |
| F. Performance | 1 | 4 | 6 | 4 | 12 |
| G. Logging & Observability | 5 | 8 | 10 | 8 | 10 |
| H. Testing Gaps | 2 | 4 | 3 | 3 | 9 |
| I. Stack Script | 0 | 1 | 1 | 0 | 4 |
| J. CI/CD Pipeline | 0 | 1 | 1 | 0 | 3 |
| K. Agent Config Guards | 0 | 1 | 1 | 1 | 2 |
| L. Refactoring | 1 | 2 | 2 | 0 | 0 |
| M. Configuration | 0 | 0 | 2 | 2 | 3 |
| N. Admin Portal HTML/JS | 1 | 2 | 2 | 2 | 3 |
| O. Docker & Infrastructure | 1 | 3 | 1 | 1 | 4 |
| P. Database & Migrations | 0 | 1 | 2 | 0 | 4 |
| **TOTAL** | **12** | **35** | **46** | **33** | **96** |

**Overall Assessment:** The platform has a **mature, well-architected foundation** with strong security practices (CSRF, SSRF, encryption, header stripping), clean dependency injection, and comprehensive observability infrastructure. The main areas requiring attention are: MCP client pool performance, rate limiter dead code, testing coverage gaps (40-50%), and inline HTML/JS extraction.

---

## 2. Top 20 Priority Fixes

| Rank | Finding | Area | Severity | Effort | Risk |
|------|---------|------|----------|--------|------|
| 1 | MCP pool pings all cached clients sequentially on every lookup | Performance | CRITICAL | Medium | Adds 0.5-6s latency per request |
| 2 | Per-path rate limits are dead code (all endpoints get 60/min) | Security | HIGH | Low | OAuth endpoint gets 60/min instead of 5/min |
| 3 | Empty credential_encryption_key accepted in production | Security | HIGH | Low | Fernet init fails at runtime, not startup |
| 4 | Agent API auth skipped when AGENT_INTERNAL_API_KEY unset | Security | HIGH | Low | API open to internet if env var missing |
| 5 | MCP pool eviction never fires (timestamps not populated) | Performance | HIGH | Low | Clients accumulate indefinitely |
| 6 | prompt_history grows unbounded during plan execution | Performance | HIGH | Medium | Memory + token cost increase |
| 7 | Zero integration tests for 132 admin portal endpoints | Testing | CRITICAL | High | Bugs in primary management UI |
| 8 | Zero tests for platform adapters (Telegram, Scheduler) | Testing | CRITICAL | Medium | Production entry points untested |
| 9 | No request-level metrics at HTTP entry point | Observability | CRITICAL | Low | Cannot measure throughput/error rate |
| 10 | Architecture validator not enforced in CI | Config Guards | HIGH | Low | Layer violations can merge |
| 11 | MCP server URLs not checked for SSRF | Security | MEDIUM | Low | Admin can target internal services |
| 12 | HomeyTool HTTP client never closed on shutdown | Performance | HIGH | Low | Connection leak |
| 13 | WebFetcher cache eviction full dir sort on every write | Performance | HIGH | Low | 1000 stat() calls per cache write |
| 14 | 2,882+ lines inline HTML/CSS/JS in Python admin modules | Refactoring | CRITICAL | High | Maintainability, testability |
| 15 | Request bodies with credentials captured in OTel spans | Security | MEDIUM | Low | Secrets in telemetry |
| 16 | No per-conversation trace aggregation endpoint | Observability | CRITICAL | Medium | Cannot diagnose conversation failures |
| 17 | 80% of tools lack dedicated tests | Testing | HIGH | High | Tool failures undetected |
| 18 | Multiple unmanaged Qdrant client instances | Architecture | HIGH | Medium | Connection exhaustion under load |
| 19 | Missing global execution timeout in AgentService | Components | CRITICAL | Medium | Runaway requests possible |
| 20 | Docker images use mutable tags (litellm, qdrant) | Infrastructure | HIGH | Low | Supply chain risk |

---

## 3. Full Findings by Area

### A. Architecture

**Auditor:** Opus | **Grade:** A-

The 4-layer modular monolith (interfaces -> orchestrator -> modules -> core) is **structurally sound**. The built-in `ArchitectureValidator` passes with 0 violations. Protocol-based DI is consistently applied.

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| A1 | HIGH | Multiple unmanaged Qdrant clients -- `CodeIndexer` has no `close()`, `RAGManager` creates own client bypassing `Settings` | `modules/indexer/ingestion.py:20`, `modules/rag/__init__.py:37` |
| A2 | MEDIUM | Orchestration logic lives in `core/agents/`, `core/routing/`, `core/skills/` -- not in `orchestrator/` as documented | `core/agents/planner.py`, `core/routing/unified_orchestrator.py` |
| A3 | MEDIUM | `AgentService` still 1790 lines despite decomposition (PR #183) | `core/runtime/service.py` |
| A4 | MEDIUM | Modules use `os.getenv()` instead of `Settings` for config (Qdrant URL, collection name) | `modules/rag/__init__.py:19`, `modules/indexer/ingestion.py:19` |
| A5 | LOW | `TokenManager` not behind Protocol (inconsistency with other DI patterns) | `core/providers.py:105-116` |
| A6 | LOW | `shared/` is undocumented 5th cross-cutting layer (Layer 0) | `shared/` directory |

**Positive:**
- Architecture validator enforces layer rules via AST parsing
- ServiceFactory provides context-scoped service creation with shared resources
- Provider registration centralized in `orchestrator/startup.py`
- MemoryStore has tenant isolation with caller frame inspection warning
- WebFetcher SSRF protection is multi-layered
- MCP client pool has negative caching for failed connections

---

### B. Documentation vs Code

**Auditor:** Sonnet | **Grade:** B+

| # | Severity | Finding | Location |
|---|----------|---------|----------|
| B1 | HIGH | `admin_debug.py` and `admin_sessions.py` referenced in CLAUDE.md but files do not exist | CLAUDE.md:730-744 |
| B2 | MEDIUM | Supervisor file path wrong: docs say `supervisors.py`, code has `supervisor_plan.py` + `supervisor_step.py` | CLAUDE.md:410 |
| B3 | MEDIUM | Navigation items in docs don't match actual `ADMIN_NAV_ITEMS` in `admin_shared.py` | CLAUDE.md:752-762 |
| B4 | LOW | Test file count: docs say "37+" but actual count is 41 | CLAUDE.md:439 |
| B5 | LOW | Template list incomplete (missing `diagnostics_dashboard.html`, `price_tracker_dashboard.html`) | CLAUDE.md:740-743 |
| B6 | LOW | Additional slash commands `/deepreview`, `/githubsecurityfix` not documented | CLAUDE.md:685 |

**Positive:** Architecture constraints, tool registration, skill format, code standards, directory structure all accurately documented.

---

### C. Security

**Auditor:** Opus | **Grade:** B+

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| C1 | HIGH | Per-path rate limits dead code -- all endpoints get 60/min default instead of configured limits (OAuth 5/min, admin 10/min) | `core/middleware/rate_limit.py:42-54` |
| C2 | HIGH | Empty `credential_encryption_key` accepted silently at startup | `core/runtime/config.py:149` |
| C3 | HIGH | Agent API auth skipped when `AGENT_INTERNAL_API_KEY` not set -- logs warning but allows access | `interfaces/http/app.py:91-97` |
| C4 | MEDIUM | MCP server URLs not validated against internal networks (no SSRF check) | `interfaces/http/admin_mcp.py:216-218` |
| C5 | MEDIUM | Git branch parameter not validated for injection | `core/tools/git_clone.py:318-321` |
| C6 | MEDIUM | Request bodies with credentials captured in OTel span attributes | `interfaces/http/app.py:301-354` |
| C7 | MEDIUM | Rate limiter trusts `X-Forwarded-For` (mitigated by container isolation in prod) | `core/middleware/rate_limit.py:54` |
| C8 | MEDIUM | CSP allows `unsafe-inline` for scripts | `interfaces/http/app.py:221` |
| C9 | LOW | Diagnostic health endpoint unauthenticated (minimal exposure) | `interfaces/http/admin_api.py:1085-1089` |
| C10 | LOW | Docker images use mutable tags | `docker-compose.yml:6,139` |

**Positive (14 items):**
- Database role as sole authority (header/JWT claims never trusted)
- `secrets.compare_digest()` constant-time comparison everywhere
- CSRF double-submit cookie with HMAC-SHA256 (45 tests)
- SSRF protection multi-layer (scheme + hostname + DNS + redirect)
- Traefik header stripping prevents auth bypass
- OAuth PKCE with S256 code challenge
- Security event logging with SIEM-ready JSON
- Claude Code tool sandboxing (no `--dangerously-skip-permissions`)
- Fernet encryption for all credential types at rest
- TLS 1.2+ with strong ciphers via Traefik
- Generic error responses to clients
- Log injection prevention via `sanitize_log()`
- Context-isolated workspaces with path traversal validation
- Multi-tenant token isolation scoped to `context_id`

---

### D. Component Functionality

**Auditor:** Sonnet | **Overall Maturity:** HIGH

| Component | Maturity | Key Strengths | Critical Gaps |
|-----------|----------|---------------|---------------|
| Orchestrator (AgentService) | HIGH | Adaptive loop (3 replans), HITL, full OTel | No global timeout, fire-and-forget memory |
| Planner | HIGH | JSON extraction with retry, input sanitization | Hardcoded 4000 char limit, no skill validation |
| Supervisors | HIGH | 4-level outcomes, lenient defaults, suggested fixes | Hardcoded retry escalation, no LLM timeout |
| Step Executor | MEDIUM-HIGH | Streaming tools, 120s timeout, model routing | Hardcoded context injection, fragile error detection |
| Skill System | EXCELLENT | Tool scoping, context validation, HITL, rate limiting | No execution timeout, missing metrics |
| LiteLLM Client | EXCELLENT | Reasoning model support, observability, token stripping | No retry for transient failures, no cancellation |
| Tools | MEDIUM-HIGH | Consistent base class, activity hints | Inconsistent error handling, no circuit breakers |
| Conversation Management | HIGH | Clean hierarchy, history injection | No cleanup/TTL, no pagination |
| SSE Streaming | HIGH | Verbosity filtering, context resolution | No backpressure, no heartbeat |
| MCP Client | MEDIUM | Connection pooling, dynamic discovery | No health checks, no error recovery |

**Priority Fixes:**
- P0: Add global execution timeout to AgentService
- P0: Standardize tool error responses
- P1: Add LiteLLM retry for 429/502/503
- P2: Implement circuit breakers for external services

---

### E. Dead Code

**Auditor:** Sonnet

| # | Severity | Finding | File |
|---|----------|---------|------|
| E1 | MEDIUM | 2 unused registered tools: `test_runner`, `update_memory` (no skill references) | `config/tools.yaml:29-32,64-67` |
| E2 | LOW | 3 deprecated env vars still in `.env.template`: `AGENT_PRICE_TRACKER_FROM_EMAIL`, `AGENT_CONTEXT7_MCP_URL`, `AGENT_CONTEXT7_API_KEY` | `.env.template:151,177-178` |
| E3 | LOW | `respx` dev dependency unused in codebase | `pyproject.toml` |
| E4 | LOW | `core/debug/` module deprecated with compat shim (already replaced by `core/observability/debug_logger.py`) | `core/debug/logger.py` |

**Positive:** No orphaned tool implementations, clean dependency usage, proper deprecation patterns, all scripts actively used.

---

### F. Performance

**Auditor:** Opus

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| F1 | CRITICAL | MCP pool pings all cached clients sequentially on every lookup (0.5-6s latency) | `core/mcp/client_pool.py:90-126` |
| F2 | HIGH | MCP pool eviction never fires -- `_timestamps` never populated when clients cached | `core/mcp/client_pool.py:47,179` |
| F3 | HIGH | `prompt_history` grows unbounded during plan execution (memory + token cost) | `core/runtime/service.py:1246` |
| F4 | HIGH | HomeyTool HTTP client never closed on shutdown | `core/tools/homey.py:131-146` |
| F5 | HIGH | WebFetcher cache eviction does full dir sort (1000 stat() calls) on every write | `modules/fetcher/__init__.py:134-148` |
| F6 | MEDIUM | `HomeyTool._session_cache` has no TTL eviction | `core/tools/homey.py:134,275` |
| F7 | MEDIUM | MCP pool `_locks` defaultdict grows monotonically (never cleaned) | `core/mcp/client_pool.py:46` |
| F8 | MEDIUM | `ServiceFactory.create_service` makes multiple sequential DB queries per request | `core/runtime/service_factory.py:103-172` |
| F9 | MEDIUM | `RotatingFileHandler` sync disk I/O in debug logging path | `core/observability/debug_logger.py:60-66` |
| F10 | MEDIUM | `ToolPermission.context_id` may lack index (queried every `create_service` call) | `core/runtime/service_factory.py:103` |
| F11 | MEDIUM | Tool registry loaded twice at startup | `app.py:439`, `service_factory.py:61` |
| F12 | LOW | `inspect.currentframe()` on every MemoryStore init without `context_id` | `core/runtime/memory.py:76-85` |
| F13 | LOW | WebFetcher client has no client-level timeout default | `modules/fetcher/__init__.py:80-86` |
| F14 | LOW | `read_debug_logs` loads entire JSONL file into memory | `core/observability/debug_logger.py:116` |

**Positive:** Shared HTTP clients (LiteLLM, WebFetcher), well-configured DB pool (`pool_pre_ping`, `pool_recycle`), shared Qdrant client, proper `asyncio.to_thread()` for blocking I/O, background memory persistence, negative MCP cache, parallel skill loading.

---

### G. Logging & Observability

**Auditor:** Sonnet | **Grade:** B+

**Critical Gaps:**

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| G1 | CRITICAL | No request-level metrics at HTTP entry (functions defined but never called) | `core/observability/metrics.py:234-263` |
| G2 | CRITICAL | No per-conversation trace aggregation endpoint | `interfaces/http/admin_api.py` |
| G3 | CRITICAL | Missing tool timeout instrumentation (timeouts look like generic errors) | `core/runtime/tool_runner.py:143-159` |
| G4 | CRITICAL | No latency percentiles in OTel metrics (no explicit histogram buckets) | `core/observability/metrics.py:174-178` |
| G5 | CRITICAL | Debug logging toggle not exposed in API | `core/observability/debug_logger.py:171-193` |

**High:**
- `skill_step_counter` defined but never called (H1)
- No span for conversation history loading (H2)
- Security events outside trace context not correlated (H3)
- No error rate alerting threshold (H4)
- No trace span for plan generation (H6)
- No `X-Request-ID` header in responses (H8)

**Unused OTel Metrics:** `request_counter`, `skill_step_counter`, `active_requests_gauge` -- defined but never called.

**Positive:** Clean 3-layer architecture (tracing, metrics, debug logs), LiteLLM/SQLAlchemy auto-instrumented, file rotation, async-first background writers, comprehensive diagnostic API (14 endpoints), structured error codes with recovery hints, trace_id stored in Message model.

---

### H. Testing Gaps

**Auditor:** Sonnet | **Estimated Coverage:** 40-50%

| # | Severity | Finding |
|---|----------|---------|
| H1 | CRITICAL | Zero integration tests for 132 admin portal endpoints |
| H2 | CRITICAL | Zero tests for platform adapters (Telegram, Scheduler) |
| H3 | HIGH | 80% of tools lack dedicated tests (12 of 15) |
| H4 | HIGH | Security-critical code paths under-tested (API key auth, command injection, MCP schema) |
| H5 | HIGH | Core runtime modules lack isolated tests (tool_runner, persistence, hitl) |
| H6 | HIGH | No semantic/E2E regression tests found |
| H7 | MEDIUM | CI testpaths incomplete -- missing `core/observability/tests`, `core/context/tests` |
| H8 | MEDIUM | No FastAPI TestClient fixture for integration tests |
| H9 | MEDIUM | No mock MCP server fixture |

**Coverage by Layer:**

| Layer | Estimated Coverage |
|-------|-------------------|
| Core Agents (planner, executor, supervisors) | 85% |
| Core Tools | 20% |
| Core Runtime | 40% |
| Interfaces HTTP | 10% |
| Interfaces Telegram/Scheduler | 0% |
| Modules | 75% |
| Stack CLI | 80% |

**Positive:** 890+ unit tests in `core/tests/`, CSRF has 45 tests, SSRF has 16 tests, skills infrastructure well-tested, content classifier (29 tests), chunk filter (30 tests), price tracker module (39 tests).

---

### I. Stack Script

**Auditor:** Sonnet | **Grade:** A-

| # | Severity | Finding |
|---|----------|---------|
| I1 | HIGH | Missing validation for critical env vars at startup (e.g., `AGENT_CREDENTIAL_ENCRYPTION_KEY`) |
| I2 | MEDIUM | Architecture check not included in `stack check` CI flow |

**Positive:** No `shell=True` usage, clean Typer+Rich CLI, proper error handling, good UX consistency, subprocess commands well-structured.

---

### J. CI/CD Pipeline

**Auditor:** Sonnet | **Grade:** B+

| # | Severity | Finding |
|---|----------|---------|
| J1 | HIGH | No automated deployment in CI (manual `stack deploy` required) |
| J2 | MEDIUM | No Docker BuildKit cache in CI workflow |

**Positive:** CodeQL security scanning, Trivy container scanning, pip-audit dependency checks, coverage reporting.

---

### K. Agent Config & Architecture Guards

**Auditor:** Sonnet

| # | Severity | Finding |
|---|----------|---------|
| K1 | HIGH | Architecture validation NOT in CI -- violations can merge to main | `.github/workflows/ci.yml` |
| K2 | MEDIUM | `TYPE_CHECKING` imports bypass validator (could hide layer violations) | `core/validators/architecture.py` |
| K3 | LOW | 4 stale "Builder" references in agent config docs | `.claude/agents/*.md` |

**Positive:** Zero current violations, architecture validator uses AST parsing.

---

### L. Refactoring Opportunities

**Auditor:** Sonnet

| # | Severity | Finding | File |
|---|----------|---------|------|
| L1 | CRITICAL | 2,882+ lines inline HTML/CSS/JS in Python admin modules (largest: `admin_diagnostics.py` with 1,416 lines of HTML) | `interfaces/http/admin_diagnostics.py` |
| L2 | HIGH | Deep nesting up to 10 levels in `SkillExecutor.execute_stream()` | `core/skills/executor.py` |
| L3 | HIGH | 30+ hard-coded config values (timeouts, limits, thresholds) scattered across codebase | Various |
| L4 | MEDIUM | God classes: `AgentService` (24 methods, 1790 lines), `HomeyTool` (22 methods) | `core/runtime/service.py`, `core/tools/homey.py` |
| L5 | MEDIUM | Inconsistent error handling patterns between tools (exceptions vs "Error:" strings) | `core/tools/` |

---

### M. Configuration Review

**Auditor:** Sonnet

| # | Severity | Finding |
|---|----------|---------|
| M1 | MEDIUM | 12 unused/stale variables in `.env.template` (Context7, price tracker email, etc.) |
| M2 | MEDIUM | 7 variables used in code but missing from `.env.template` |
| M3 | LOW | Naming inconsistency (`AGENT_*` prefix vs direct names) |
| M4 | LOW | Duplicate Qdrant config vars (`QDRANT_URL` in both Settings and `os.getenv()` in modules) |

**Positive:** `.env.template` exists with good documentation, `Settings` class with Pydantic validation, sensible defaults for most config.

---

### N. Admin Portal HTML/JS

**Auditor:** Sonnet

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| N1 | CRITICAL | OAuth flow missing status refresh after authorization (status shows "pending" until page reload) | `templates/admin_mcp.html` |
| N2 | HIGH | Missing pagination on large lists (conversations, users, contexts) | Multiple admin modules |
| N3 | HIGH | Accessibility gaps: missing ARIA labels, no keyboard navigation on modals | Multiple templates |
| N4 | MEDIUM | Some fetch calls without response checking (silent failures) | Various admin modules |
| N5 | MEDIUM | No confirmation on all destructive actions (some delete operations lack confirm dialog) | Various |
| N6 | LOW | Hardcoded URLs in some JavaScript fetch calls | Various |
| N7 | LOW | Inconsistent loading state patterns between modules | Various |

**Positive:** `escapeHtml()` used consistently (good XSS protection), CSRF tokens on all mutation endpoints, CodeMirror integration for code editing.

---

### O. Docker & Infrastructure

**Auditor:** Sonnet

| # | Severity | Finding | File |
|---|----------|---------|------|
| O1 | CRITICAL | Docker socket exposure via Traefik (mitigated by `:ro` mount) | `docker-compose.yml` |
| O2 | HIGH | Image pinning to mutable tags (`litellm:main-latest`, `qdrant:latest`) | `docker-compose.yml:6,139` |
| O3 | HIGH | No backup strategy documented for PostgreSQL data volumes | Infrastructure docs |
| O4 | HIGH | `vault-mcp` container runs as root | `services/vault-mcp/Dockerfile` |
| O5 | MEDIUM | No resource limits on some dev-only services | `docker-compose.dev.yml` |
| O6 | LOW | No image pruning strategy documented | Ops docs |

**Positive:** Good dev/prod separation, resource limits on prod services, log rotation configured, internal services not exposed (use `expose` not `ports`), POSTGRES_PASSWORD required via `${...:?}` syntax.

---

### P. Database & Migrations

**Auditor:** Sonnet

| # | Severity | Finding | File |
|---|----------|---------|------|
| P1 | HIGH | Unbounded SELECT in admin endpoints (no LIMIT on conversation/user lists) | Multiple admin modules |
| P2 | MEDIUM | Missing index on `users.active_context_id` (used in user lookups) | `core/db/models.py` |
| P3 | MEDIUM | No database backup/restore automation | Infrastructure |

**Positive:** Linear migration chain (no forks), all migrations reversible, good cascade rules, FK indexes present, Fernet encryption at rest, composite index on `(session_id, created_at)` for message loading.

---

## 4. Cross-Cutting Themes

### Theme 1: Security Enforcement Gaps
Rate limiter per-path limits are dead code (C1). API auth is optional when env var unset (C3). Startup validation for critical secrets is missing (C2, I1). MCP URLs not SSRF-checked (C4). **Pattern:** Security mechanisms exist but are not fully wired up.

### Theme 2: Resource Lifecycle Issues
MCP pool never evicts (F2). HomeyTool HTTP client never closed (F4). Unmanaged Qdrant clients in modules (A1). MCP locks grow monotonically (F7). **Pattern:** Resources are created but cleanup paths are incomplete.

### Theme 3: Missing CI Enforcement
Architecture validator not in CI (K1). No deployment automation (J1). Testpaths incomplete (H7). Coverage thresholds not enforced. **Pattern:** Quality gates exist locally but are not enforced in the pipeline.

### Theme 4: Template Extraction Debt
2,882+ lines of inline HTML/CSS/JS (L1). Some admin modules exceed 1,900 lines with most being HTML. Templates partially extracted but migration incomplete. **Pattern:** HTML generation mixed with Python logic reduces testability and maintainability.

### Theme 5: Observability Infrastructure vs Usage Gap
OTel metrics are defined but 3 are never called (G1). Span coverage is incomplete at HTTP entry and plan generation layers. Debug toggle requires DB access. **Pattern:** Infrastructure is excellent but not fully wired to production code paths.

### Theme 6: Configuration Fragmentation
Modules use `os.getenv()` bypassing `Settings` (A4, M4). 30+ hardcoded values (L3). 12 stale env vars in template (M1). **Pattern:** Configuration is spread across `Settings`, `os.getenv()`, and hardcoded values without a single source of truth.

---

## 5. Phased Roadmap

### Phase 1: Critical Security & Performance (1-2 weeks)

**Goal:** Fix issues that affect production stability and security.

1. **Wire up per-path rate limits** (C1) -- Low effort, high impact
2. **Validate `AGENT_INTERNAL_API_KEY` and `credential_encryption_key` at startup** (C2, C3) -- Low effort
3. **Fix MCP pool: parallel pings + populate timestamps** (F1, F2) -- Medium effort
4. **Add SSRF validation to MCP server URLs** (C4) -- Low effort
5. **Add credential endpoints to OTel span body skip list** (C6) -- Low effort
6. **Close HomeyTool HTTP client on shutdown** (F4) -- Low effort
7. **Fix WebFetcher cache eviction (threshold-based)** (F5) -- Low effort

### Phase 2: Observability & Testing (2-4 weeks)

**Goal:** Close monitoring gaps and establish integration test foundation.

8. **Wire up unused OTel metrics** (G1) -- request_counter, skill_step_counter, active_requests_gauge
9. **Add per-conversation trace endpoint** (G2) -- `/api/conversations/{id}/traces`
10. **Add debug toggle API endpoint** (G5)
11. **Add architecture validation to CI** (K1)
12. **Add FastAPI TestClient fixture + admin API integration tests** (H1, H8)
13. **Add adapter tests (Telegram, Scheduler)** (H2)
14. **Add global execution timeout to AgentService** (D critical)
15. **Cap prompt_history size during plan execution** (F3)

### Phase 3: Code Quality & Documentation (4-6 weeks)

**Goal:** Reduce technical debt and improve maintainability.

16. **Extract remaining inline HTML to templates** (L1) -- Focus on `admin_diagnostics.py`
17. **Add tool tests for azure_devops, git_clone, claude_code** (H3)
18. **Fix documentation mismatches** (B1-B3) -- Update CLAUDE.md admin portal section
19. **Remove dead code** (E1-E4) -- Unused tools, deprecated env vars, respx dependency
20. **Consolidate config: inject Settings values into modules** (A4, M4)
21. **Add pagination to admin list endpoints** (N2, P1)
22. **Pin Docker images to SHA256 digests** (O2)
23. **Add Dockerfile USER directive to vault-mcp** (O4)

### Phase 4: Polish & Hardening (6-8 weeks)

**Goal:** Production hardening and observability maturity.

24. **Add LiteLLM retry logic for transient failures** (D component)
25. **Implement circuit breakers for external services** (D component)
26. **Add latency histogram buckets for percentile tracking** (G4)
27. **Add SSE heartbeat for long-running streams** (D component)
28. **Standardize tool error responses** (L5)
29. **Add semantic regression test suite** (H6)
30. **Reduce SkillExecutor nesting depth** (L2)
31. **Add `X-Request-ID` response header** (G observability)
32. **Set up CI coverage thresholds** (H testing)

---

## Appendix: Methodology

Each of the 16 agents independently explored the codebase using Read, Grep, Glob, and Bash tools. Findings include file:line references verified against actual source. Model assignment: Opus for Architecture (#1), Security (#3), Performance (#6); Sonnet for all others. All agents completed successfully with no errors.
