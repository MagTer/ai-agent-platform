# Comprehensive Platform Audit - 2026-02-12

## Executive Summary

16 parallel architect agents audited the entire AI Agent Platform codebase. The platform demonstrates **strong security fundamentals**, **good async patterns**, and **solid multi-tenant isolation**. However, critical structural issues -- particularly the `core.core` naming collision and the 2,552-line god class -- significantly hinder AI maintainability.

### Severity Distribution

| Area | CRITICAL | HIGH | MEDIUM | LOW | Positive |
|------|----------|------|--------|-----|----------|
| A. Architecture | 2 | 5 | 7 | 4 | 11 |
| B. Documentation | 5 | 5 | 6 | 7 | 6 |
| C. Security | 1 | 3 | 5 | 4 | 15 |
| D. Component Functionality | 3 | 5 | 3 | 0 | 6 |
| E. Dead Code | 0 | 1 | 3 | 2 | 8 |
| F. Performance | 0 | 3 | 6 | 4 | 19 |
| G. Logging & Observability | 2 | 2 | 6 | 0 | 8 |
| H. Testing Gaps | 3 | 4 | 3 | 1 | 5 |
| I. Stack CLI | 3 | 4 | 5 | 7 | 4 |
| J. CI/CD Pipeline | 2 | 1 | 3 | 0 | 6 |
| K. Agent Config & Guards | 2 | 3 | 3 | 2 | 6 |
| L. Refactoring | 2 | 2 | 3 | 0 | 3 |
| M. Configuration | 0 | 3 | 4 | 2 | 8 |
| N. Admin Portal HTML/JS | 1 | 4 | 9 | 4 | 3 |
| O. Docker & Infrastructure | 0 | 1 | 7 | 4 | 12 |
| P. Database & Migrations | 3 | 4 | 5 | 3 | 6 |
| **TOTAL** | **29** | **50** | **78** | **44** | **126** |

---

## Top 20 Priority Fixes

| # | Finding | Severity | Area | Effort | Impact |
|---|---------|----------|------|--------|--------|
| 1 | **Rename `core/runtime/` to `core/runtime/`** -- 84 imports across 37 files use confusing `core.runtime.*` pattern | CRITICAL | A,L | 2 hours | Eliminates naming confusion for all contributors and AI agents |
| 2 | **Split `AgentService` god class** (2,552 lines, 38 methods, 8+ concerns) into focused modules | CRITICAL | A,L | 3-5 days | 94% context reduction for AI modifications |
| 3 | **Add CASCADE rules to initial schema FKs** -- conversations, sessions, messages lack DB-level cascade | CRITICAL | P | 1 hour | Prevents orphaned records on raw SQL deletes |
| 4 | **Apply path-specific rate limits** -- `get_rate_limit_for_path()` defined but never called; login/OAuth get 60/min instead of 5 | HIGH | C | Low | Prevents brute-force on auth endpoints |
| 5 | **Verify Entra ID token signature** -- `jwt.decode(id_token, options={"verify_signature": False})` | CRITICAL | C | Medium | Eliminates MITM risk on token exchange |
| 6 | **Add orchestrator layer validation** -- architecture validator has NO rules for orchestrator imports | CRITICAL | K | 2 hours | Enforces core architecture invariant |
| 7 | **Fix architect.md dependency matrix** -- shows interfaces can import modules (wrong) | HIGH | K | 15 min | Prevents agents from writing violating code |
| 8 | **Add PlannerAgent tests** -- 598 lines, zero dedicated tests | CRITICAL | H | 8 hours | Prevents silent plan generation failures |
| 9 | **Add SSRF validation for MCP server URLs** -- only scheme validated, no private IP check | MEDIUM | C | Low | Prevents SSRF via admin-configured MCP servers |
| 10 | **Reuse shared httpx client for readiness probe** -- creates new TCP connection per /readyz call | HIGH | F | Low | Eliminates per-probe TCP overhead |
| 11 | **Async crash log writing** -- sync `open()` in exception handler blocks event loop | HIGH | F | Low | Prevents event loop blocking on errors |
| 12 | **Add missing env vars to .env.template** -- 14 vars used in code but undocumented | HIGH | M | 30 min | Users won't be confused by undocumented vars |
| 13 | **Cache Azure DevOps connection per context** -- new Connection created per tool call | HIGH | F | Medium | 15x fewer connection setups for team_summary |
| 14 | **Add health checks for LiteLLM and Open WebUI** -- silent failures undetected | MEDIUM | O | Low | Auto-recovery from crashes |
| 15 | **Add input sanitization to stack CLI auth** -- token written to .env without validation | CRITICAL | I | Low | Prevents command injection via .env |
| 16 | **Add default timeout to checks.py `_run_cmd()`** -- quality check commands can hang indefinitely | CRITICAL | I | Low | Prevents CI/dev hangs |
| 17 | **Exclude credential endpoints from telemetry body capture** | LOW | C | Low | Prevents secret leakage to span logs |
| 18 | **Pin Docker images to SHA digests** -- litellm and qdrant use mutable `:latest` tags | MEDIUM | O | Low | Prevents supply chain attacks |
| 19 | **Add latency percentiles to diagnostics** -- no p50/p95/p99 metrics | CRITICAL | G | 1 day | Enables proactive performance monitoring |
| 20 | **Integrate error codes with tracing spans** -- 90+ error types exist but never called | CRITICAL | G | 2 days | Enables intelligent error classification |

---

## Full Findings by Area

### A. Architecture

**Agent:** Opus | **Grade: B-**

**CRITICAL:**
1. `core/runtime/` naming collision -- 37 files use stutter-path `core.runtime.config`, `core.runtime.service`, etc. Inner `core/` should be renamed to `runtime/` or split into purpose-named dirs (`core/llm/`, `core/memory/`, etc.)
2. `AgentService` god class -- 2,552 lines, 38 methods, 8+ distinct concerns (planning, execution, HITL, persistence, memory, routing, tool calling, completion). Should split into ~8 focused modules of 150-400 lines each.

**HIGH:**
- Layer violation: 4 test files in `core/tests/` import from `interfaces/` and `orchestrator/`
- Undocumented `shared/` and `utils/` layers (not in pyproject.toml packages)
- Dual routing systems (`core/core/routing.py` vs `core/routing/unified_orchestrator.py`)
- 292 occurrences of `Any` type across 63 files (project standard says "never use Any")
- Unclear boundary between `core/agents/` and `core/skills/`

**Positive:** Protocol-based DI excellent, no cross-module imports, template extraction done well, async-first throughout.

---

### B. Documentation vs Code

**Agent:** Sonnet | **Grade: 7/10**

**CRITICAL:**
- Module paths in docs use `agent.core.*` but code uses `core.runtime.*`
- Docs reference SQLite but code uses PostgreSQL exclusively
- `core/runtime/` pattern exists in code but not documented
- Skill frontmatter docs show `inputs:` but code uses `variables:`
- Database config docs say `AGENT_DATABASE_URL` but code reads `POSTGRES_URL`

**Positive:** Multi-tenant architecture well-documented, skills-native execution accurate, agent workflow clear.

---

### C. Security

**Agent:** Opus | **Grade: B+**

**CRITICAL:**
- Entra ID token signature verification skipped (`verify_signature: False`)

**HIGH:**
- Admin JWT cookie `secure` flag depends on spoofable header, not environment setting
- Rate limiter path-specific limits defined but never applied (flat 60/min for all)
- Diagnostic API `verify_api_key_or_admin` passes `request=None` (dead code, but confusing)

**MEDIUM:**
- CSP allows `unsafe-inline` for scripts and styles
- CORS allows all methods and headers (`["*"]`)
- Credential encryption key defaults to empty string in dev
- MCP server URL validation insufficient for SSRF (no private IP check)
- Deprecated `datetime.utcnow()` usage

**Positive (15):** Header-trust verification (DB authoritative), Traefik header stripping, CSRF with HMAC-signed double-submit cookies, constant-time API key comparison, comprehensive SSRF protection in WebFetcher, Fernet encryption at rest, subprocess safety (no shell=True), git clone URL validation, security event logging, log injection prevention, production secret validation, XSS output encoding, TLS 1.2+ with strong ciphers, proper connection pooling, no privileged containers.

---

### D. Component Functionality

**Agent:** Sonnet | **Overall Maturity: HIGH**

| Component | Maturity | Key Issue |
|-----------|----------|-----------|
| Skill System | EXCELLENT | No tool scoping tests |
| Security Framework | EXCELLENT | -- |
| Observability | EXCELLENT | Error codes unused |
| LiteLLM Client | EXCELLENT | -- |
| Orchestrator | HIGH | TODO refactor noted |
| Tool Framework | HIGH | 11/18 tools untested |
| Azure DevOps | HIGH | WIQL injection risk |
| MCP Client | MEDIUM | Connection state races |
| Conversation Mgmt | MEDIUM | HITL resume untested |
| RAG Module | LOW | Not found/incomplete |

---

### E. Dead Code

**Agent:** Sonnet | **Risk: LOW**

**HIGH:** Context7 module (200+ lines) -- partial integration, never registered, uses deprecated pattern. Remove and let users add via MCP admin page.

**MEDIUM:** `EditFileTool` and `ListDirectoryTool` exported but not registered; `clock` and `calculator` registered but used by zero skills; deprecated config settings (`sqlite_state_path`, `contexts_dir`, `email_from_name`).

**Positive:** All registered tools have implementations, all skills reference valid tools, dependencies lean, no duplicate definitions.

---

### F. Performance

**Agent:** Opus | **Grade: B+**

**HIGH:**
- Sync `open()` in exception handler blocks event loop (`app.py:184`)
- Readiness probe creates new httpx.AsyncClient per request (`app.py:610`)
- Azure DevOps creates new Connection per tool call (`azure_devops.py:397`)

**MEDIUM:** 5 unbounded caches (McpClientPool._locks, HomeyTool._session_cache, etc.), sync template `read_text()` on every admin request, missing pagination on context detail (loads ALL conversations), WebFetcher cache eviction scans all files, rate limiter is a no-op.

**Positive (19):** DB connection pooling (pool_size=10, max_overflow=20), shared Qdrant client, shared LiteLLM HTTP client (200/50 limits), MCP client pool with TTL eviction, background memory persistence, proper shutdown lifecycle, LiteLLM warmup in background, DB retention cleanup, Homey device cache with 36h TTL, streaming with `asyncio.sleep(0)`, excellent N+1 avoidance in admin queries, comprehensive database indices.

---

### G. Logging & Observability

**Agent:** Sonnet | **Grade: B-**

**CRITICAL:**
- No latency percentiles (p50/p95/p99) -- cannot detect performance degradation
- Error codes exist (90+ types) but never integrated with tracing spans

**HIGH:**
- No per-conversation trace aggregation
- No conversation diagnostics endpoint

**MEDIUM:** Inconsistent span naming, debug_logs.conversation_id not indexed, spans.jsonl grows unbounded (62MB, 126k spans), no DB query spans, no alerting infrastructure.

**Positive:** Multi-exporter tracing, graceful degradation, async file I/O for spans, dual-path security logging, debug mode with DB persistence, diagnostic API for AI self-diagnosis, interactive waterfall UI.

---

### H. Testing Gaps

**Agent:** Sonnet | **Coverage: ~60%**

**CRITICAL:**
- PlannerAgent (598 lines) -- zero dedicated tests
- StepExecutorAgent -- no agent-level tests
- ResponseAgent (~200 lines) -- zero tests

**HIGH:**
- Module layer only 17% tested (4/23 files)
- 11/18 tools have zero tests (web_search, web_fetch, azure_devops, etc.)
- Admin API endpoints: 15 files but only 3 test files
- No integration tests for diagnostic API key auth

**Positive:** 756 tests passing, comprehensive CSRF tests (18 cases), git_clone security tests (52 cases), good credential service tests, robust CI with xdist parallel.

---

### I. Stack CLI

**Agent:** Sonnet | **Grade: B+**

**CRITICAL:**
- Shell injection risk in `auth.py` -- token written to .env without sanitization
- Missing timeout on `checks.py:_run_cmd()` -- quality checks can hang indefinitely
- `qdrant restore` has `rm -rf` with only single confirmation

**HIGH:**
- `tail_logs(follow=True)` has no timeout (hangs automation)
- Fragile error handling assumes stderr is bytes
- `repo save` stages ALL files with `git add -A` (could commit secrets)
- No timeout on Docker SDK calls

**Positive:** 98% timeout coverage on subprocess calls, strong safety guardrails (branch protection, confirmation prompts), typed exceptions, good separation of concerns.

---

### J. CI/CD Pipeline

**Agent:** Sonnet | **Grade: B+**

**CRITICAL:**
- Branch protection not enforced via GitHub settings (relies on convention)
- No Docker BuildKit/layer caching (builds 5-10 min instead of ~1 min)

**HIGH:** Coverage reports uploaded as artifacts only, no historical tracking.

**Positive:** 4 separate workflows (CI, Trivy, CodeQL, dependency audit), quality gate pattern, container security scanning, SAST with security-extended queries, weekly dependency audits, zero-downtime deployments.

---

### K. Agent Config & Guards

**Agent:** Sonnet | **Grade: B**

**CRITICAL:**
- No orchestrator layer validation in architecture validator
- Dependency matrix in `architect.md` is incorrect (shows interfaces can import modules)

**HIGH:**
- No TYPE_CHECKING block validation
- Architecture validator not documented in agent configs
- Validator returns success even with baselined violations (no warnings in CI)

**Positive:** Architecture validator functional and CI-integrated, empty baseline (no accepted violations), agent role separation clear, platform-specific security checklist comprehensive.

---

### L. Refactoring Opportunities

**Agent:** Sonnet | **Key Theme: AI Maintainability**

**CRITICAL:**
- `core.core` naming -- 84 imports across 37 files (rename to `core/runtime/`)
- `AgentService` split -- 2,552 lines into orchestration, HITL, persistence, tool execution, completion modules

**HIGH:**
- `admin_price_tracker.py` -- 2,623 lines with embedded HTML (template exists but unused)
- 8 functions >100 lines (all in AgentService)

**MEDIUM:** 144 generic `except Exception` occurrences, hardcoded constants without config, duplicated context resolution in 4 admin files.

**Impact:** After refactoring, modifying HITL logic requires reading 150 lines instead of 2,552 (94% context reduction).

---

### M. Configuration Review

**Agent:** Sonnet

**HIGH:**
- 14 env vars used in code but missing from `.env.template` (POSTGRES_URL, QDRANT_URL, SEARXNG_URL, etc.)
- Model name config mismatch (`.env.template` shows `AGENT_LITELLM_MODEL`, code uses `AGENT_MODEL_PLANNER/SUPERVISOR/AGENTCHAT/COMPOSER`)
- No production validation for `ADMIN_API_KEY` and `DIAGNOSTIC_API_KEY`

**MEDIUM:** Inconsistent `AGENT_` prefix usage, redundant OAuth URL config, email backward compatibility overhead, internal service URLs exposed as required config.

**Positive:** Pydantic Settings auto-loading, sensible defaults everywhere, production secret validation, SystemConfig table for runtime toggles, per-context OAuth token isolation.

---

### N. Admin Portal HTML/JS

**Agent:** Sonnet | **Grade: B-**

**CRITICAL:** Verify all mutation endpoints have CSRF protection (most do, but audit recommended).

**HIGH:**
- `escapeHtml()` duplicated in 5+ modules instead of centralized
- Missing server-side validation on form inputs (client-side only)
- Missing loading states on many async operations
- Inconsistent error handling (duplicate toast + inline messages)

**MEDIUM:** No pagination on large datasets, hardcoded API URLs, no confirmation on all destructive actions, missing accessibility (ARIA labels, keyboard nav), no rate limiting with backoff on polling, incomplete OAuth status UI, no search/filter on lists.

**Positive:** Centralized CSRF auto-injection via `window.fetch` override, shared layout system, good Pydantic validation on API models.

---

### O. Docker & Infrastructure

**Agent:** Sonnet | **Grade: B+**

**HIGH:** Docker socket exposed to Traefik (privilege escalation risk if compromised).

**MEDIUM:** Traefik shared between dev/prod networks, access logs not persisted, LiteLLM and Open WebUI have no health checks, non-agent containers run as root, image pinning inconsistent (`:latest` tags), no volume backup strategy, no resource limits on dev environment.

**Positive (12):** Excellent dev/prod isolation (separate DBs, volumes, networks), strong TLS config (TLS 1.2+, modern ciphers), HTTP-to-HTTPS redirect, critical header stripping (auth bypass prevention), security headers (HSTS, X-Frame-Options), comprehensive health checks on core services, agent runs as non-root, secrets not hardcoded, read-only config mounts, no privileged containers, proper restart policies, log rotation configured.

---

### P. Database & Migrations

**Agent:** Sonnet | **Grade: B+**

**CRITICAL:** 3 FKs in initial schema (conversations, sessions, messages) lack `ondelete="CASCADE"` -- ORM cascade works but raw SQL deletes leave orphans.

**HIGH:**
- Missing indices on `oauth_states.context_id` and `oauth_states.user_id`
- Unbounded query in store listing (no LIMIT)
- Unbounded query in product listing (no LIMIT)

**MEDIUM:** No encryption key rotation strategy, non-reversible data migration (credential type deletion), index naming inconsistency.

**Positive:** Clean multi-tenant architecture, composite indices for real query patterns, N+1 avoidance with subqueries, proper encryption with backward compatibility, comprehensive unique constraints, 91% FK index coverage.

---

## Cross-Cutting Themes

### Theme 1: The `core.core` Problem
Found by: Architecture, Documentation, Refactoring, Agent Config audits.
The double-nested `core/runtime/` directory creates confusion across docs, imports, validator rules, and agent configurations. **Single highest-impact rename.**

### Theme 2: God Class = AI Bottleneck
Found by: Architecture, Refactoring, Testing, Performance audits.
`AgentService` at 2,552 lines is the root cause of testing gaps (can't unit test concerns independently), performance issues (everything coupled), and AI maintainability problems (must read entire file to change one thing).

### Theme 3: Security Controls Exist but Enforcement Gaps Remain
Found by: Security, Agent Config, CI/CD audits.
Rate limiter defined but not applied. Architecture validator missing orchestrator rules. Error codes defined but not used. Pattern: **good design, incomplete wiring**.

### Theme 4: Unbounded Growth
Found by: Performance, Database, Observability audits.
Multiple caches grow without bound (McpClientPool._locks, HomeyTool._session_cache, _fernet_cache). spans.jsonl grows indefinitely (currently 62MB). Admin queries lack pagination. Context detail loads ALL conversations.

### Theme 5: Documentation Drift
Found by: Documentation, Agent Config, Configuration audits.
Docs reference wrong module paths, wrong database technology, wrong env var names. Agent configs have inconsistent architecture rules. `.env.template` missing 14 vars used in code.

---

## Phased Roadmap

### Phase 1: Critical Security & Structural Fixes (Week 1)

| Task | Effort | Area |
|------|--------|------|
| Rename `core/runtime/` to `core/runtime/` | 2 hours | A,L |
| Apply path-specific rate limits | 2 hours | C |
| Add CASCADE to initial schema FKs | 1 hour | P |
| Add orchestrator layer validation | 2 hours | K |
| Fix architect.md dependency matrix | 15 min | K |
| Add input sanitization to stack CLI auth | 30 min | I |
| Add default timeout to checks.py | 15 min | I |
| Add missing env vars to .env.template | 30 min | M |
| Pin Docker images to SHA digests | 30 min | O |

### Phase 2: God Class Decomposition (Week 2-3)

| Task | Effort | Area |
|------|--------|------|
| Extract orchestration module from AgentService | 2 days | A,L |
| Extract HITL module from AgentService | 1 day | A,L |
| Extract persistence module from AgentService | 1 day | A,L |
| Extract tool execution module from AgentService | 1 day | A,L |
| Extract price tracker admin HTML | 1 day | N |
| Update documentation for new structure | 1 day | B |

### Phase 3: Testing & Observability (Week 3-4)

| Task | Effort | Area |
|------|--------|------|
| Create PlannerAgent tests | 8 hours | H |
| Create StepExecutorAgent tests | 8 hours | H |
| Add latency percentiles to diagnostics | 1 day | G |
| Integrate error codes with tracing | 2 days | G |
| Add module-layer tests (RAG, fetcher, embedder) | 2 days | H |
| Add tool security tests (web_search, filesystem) | 1 day | H |

### Phase 4: Performance & Polish (Week 4-5)

| Task | Effort | Area |
|------|--------|------|
| Reuse shared httpx client for readiness probe | 30 min | F |
| Async crash log writing | 30 min | F |
| Cache Azure DevOps connection per context | 4 hours | F |
| Cache admin templates at import time | 1 hour | F |
| Add pagination to context detail endpoint | 2 hours | F,P |
| Add health checks for LiteLLM and Open WebUI | 30 min | O |
| Centralize escapeHtml() in admin_shared.py | 1 hour | N |
| Add server-side form validation | 2 hours | N |
| Verify Entra ID token signature | 4 hours | C |

### Phase 5: Operational Maturity (Backlog)

| Task | Effort | Area |
|------|--------|------|
| Docker socket proxy for Traefik | 4 hours | O |
| Volume backup strategy | 1 day | O |
| CI BuildKit caching | 30 min | J |
| Coverage threshold enforcement | 30 min | J |
| Nonce-based CSP (replace unsafe-inline) | 1 day | C |
| spans.jsonl rotation | 2 hours | G |
| Alerting infrastructure | 1 week | G |
| Remove Context7 dead module | 30 min | E |
| Remove unused filesystem tools | 15 min | E |
| Encryption key rotation strategy | 1 day | P |
