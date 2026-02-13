# Comprehensive Platform Audit -- 2026-02-10

**16 parallel agents | 3 Opus + 13 Sonnet | Full codebase review**

---

## 1. Executive Summary

| Area | Agent | Model | Grade | Critical | High | Medium | Low |
|------|-------|-------|-------|----------|------|--------|-----|
| A. Architecture | #1 | Opus | B+ | 0 | 1 | 4 | 3 |
| B. Documentation | #2 | Sonnet | 93% | 0 | 3 | 2 | 1 |
| C. Security | #3 | Opus | Strong | 0 | 2 | 5 | 5 |
| D. Components | #4 | Sonnet | 7.5/10 | 0 | 1 | 10 | 15 |
| E. Dead Code | #5 | Sonnet | A- | 0 | 1 | 1 | 2 |
| F. Performance | #6 | Opus | -- | 2 | 4 | 7 | 3 |
| G. Observability | #7 | Sonnet | B- | 2 | 3 | 0 | 0 |
| H. Testing | #8 | Sonnet | -- | 2 | 4 | 4 | 3 |
| I. Stack CLI | #9 | Sonnet | Good | 0 | 3 | 5 | 4 |
| J. CI/CD | #10 | Sonnet | B+ | 0 | 2 | 7 | 4 |
| K. Agent Config | #11 | Sonnet | -- | 4 | 4 | 4 | 0 |
| L. Refactoring | #12 | Sonnet | -- | 3 | 8 | 12 | 6 |
| M. Configuration | #13 | Sonnet | 61% | 0 | 2 | 3 | 0 |
| N. Admin Portal | #14 | Sonnet | -- | 1 | 5 | 6 | 4 |
| O. Docker/Infra | #15 | Sonnet | Medium | 3 | 4 | 6 | 4 |
| P. Database | #16 | Sonnet | Good | 2 | 4 | 3 | 1 |
| **TOTAL** | | | | **19** | **51** | **79** | **55** |

**Overall Platform Grade: B** -- Solid security foundations with mature architecture, but operational gaps in performance, testing, and infrastructure hardening.

---

## 2. Top 20 Priority Fixes (Ranked by Risk x Effort)

| # | Finding | Source | Severity | Effort | Area |
|---|---------|--------|----------|--------|------|
| 1 | **Missing FK indices** on conversations.context_id, sessions.conversation_id, messages.session_id | #6, #16 | CRITICAL | 15 min | Database |
| 2 | **Add composite index** on Conversation(platform, platform_id) -- full table scan on every Telegram request | #6 | CRITICAL | 15 min | Performance |
| 3 | **Enable architecture checks in CI** -- currently bypassed with skip_architecture=True | #11 | CRITICAL | 10 min | Governance |
| 4 | **Fix non-constant-time OAuth state comparison** -- `!=` instead of `secrets.compare_digest()` | #3 | HIGH | 5 min | Security |
| 5 | **Add resource limits to Docker containers** -- no memory/CPU limits, OOM risk | #15 | CRITICAL | 30 min | Infrastructure |
| 6 | **Add log rotation to Docker** -- unbounded log growth fills disk | #15 | CRITICAL | 30 min | Infrastructure |
| 7 | **WebFetcher cache uses sync file I/O** -- blocks event loop on every cache read/write | #6 | CRITICAL | 1 hr | Performance |
| 8 | **N+1 query in admin contexts** -- 1+3N queries for N contexts | #16 | CRITICAL | 2 hr | Database |
| 9 | **git_clone lacks repo URL sanitization** -- credential-embedded URLs not blocked | #3 | HIGH | 1 hr | Security |
| 10 | **MCP ping() uses heavyweight list_tools()** -- adds 2+ seconds latency per cached request | #6 | HIGH | 2 hr | Performance |
| 11 | **Cookie Secure flag is False behind Traefik** -- uses request.url.scheme instead of X-Forwarded-Proto | #3 | MEDIUM | 30 min | Security |
| 12 | **Missing timeouts on all subprocess calls** in stack CLI | #9 | HIGH | 2 hr | Stack CLI |
| 13 | **Add confirmation prompts** to import commands (openwebui import, n8n import) | #9 | HIGH | 30 min | Stack CLI |
| 14 | **Fix CI/stack pytest parity** -- CI uses `-x -n auto`, local doesn't | #9 | HIGH | 15 min | CI/CD |
| 15 | **Add smoke tests post-deploy** -- health check only verifies /health, not actual functionality | #10 | HIGH | 2 hr | CI/CD |
| 16 | **Docker socket proxy** -- Traefik has read-only socket access, can read all container env vars | #15 | CRITICAL | 3 hr | Infrastructure |
| 17 | **MemoryStore: Add retry logic** for Qdrant operations -- no retry on transient failures | #4 | HIGH | 2 hr | Components |
| 18 | **Fix fetch error handling** in admin portal -- 80%+ of fetch calls have no error handling | #14 | HIGH | 2 hr | Admin Portal |
| 19 | **Remove 4 unregistered tool classes** -- dead code (search_code, github, qa) | #5 | HIGH | 1 hr | Dead Code |
| 20 | **Add form disabled states** -- double-click creates duplicate records | #14 | CRITICAL | 1 hr | Admin Portal |

---

## 3. Full Findings Per Area

### A. Architecture (#1 -- Opus)

**Grade: B+** -- Solid 4-layer modular monolith with strong protocol-based DI.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | admin_price_tracker.py directly imports modules.price_tracker (7 violations) | interfaces/http/admin_price_tracker.py:37-39 |
| MEDIUM | `shared/` is Layer 0 but undocumented in architecture hierarchy | All layers |
| MEDIUM | `core/runtime/` nested namespace creates confusing imports (82 occurrences) | core/core/*.py |
| MEDIUM | WebFetcher cache uses sync file I/O in async context | modules/fetcher/__init__.py:114-124 |
| MEDIUM | CodeIndexer uses sync open() and os.walk() in async methods | modules/indexer/ingestion.py:90,155 |
| LOW | interfaces/protocols.py defines unused IPlatformAdapter, IAssistantClient | interfaces/protocols.py:5-27 |
| LOW | utils/ directory not in documented architecture | utils/template.py |
| LOW | Validator flags DI wiring in app.py as false positives (7 of 14 violations) | core/validators/architecture.py |

**Positive:** Zero cross-module violations, core layer integrity maintained, all 7 protocols properly registered, connection lifecycle excellent.

---

### B. Documentation vs Code (#2 -- Sonnet)

**Accuracy: 93%** -- Mostly accurate, 6 fixes needed.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | admin_dashboard.py referenced but doesn't exist (actual: admin_portal.py) | CLAUDE.md:686,115 |
| HIGH | MemoryProtocol/LLMProtocol/ToolProtocol don't exist (actual: MemoryStore/LiteLLMClient/Tool) | CLAUDE.md:287-289 |
| HIGH | core/core/app.py path wrong (actual: interfaces/http/app.py) | ARCHITECTURE.md:309 |
| MEDIUM | NAV_ITEMS shows 5 items, actual has 13 | CLAUDE.md:700-706 |
| MEDIUM | "3-layer" vs "4-layer" inconsistency | ARCHITECTURE.md:29 vs CLAUDE.md:244 |
| LOW | get_memory_store/get_tool_registry provider examples don't exist | CLAUDE.md:296-305 |

**Positive:** Skills system 100% accurate, tool registration 100%, StepOutcome 100%, ContextService methods 100%.

---

### C. Security (#3 -- Opus)

**Grade: Strong** -- Mature security practices with 20+ positive findings.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | Non-constant-time OAuth state comparison (timing attack risk) | admin_auth_oauth.py:168 |
| HIGH | git_clone doesn't validate URLs for embedded credentials or attacker-controlled servers | git_clone.py:92-93 |
| MEDIUM | JWT cookie Secure flag is False behind Traefik TLS termination | admin_auth_oauth.py:392 |
| MEDIUM | Same Secure flag issue for OAuth state cookie | admin_auth_oauth.py:95-96 |
| MEDIUM | Rate limiting disabled when environment="test" | app.py:137 |
| MEDIUM | Default database URL has hardcoded postgres:postgres credentials | engine.py:7-8 |
| MEDIUM | Email tool could send to injected address if metadata spoofed | send_email.py:77 |
| LOW | Dev mode allows unauthenticated API access | app.py:83-90 |
| LOW | CORS allows all methods/headers | app.py:152-154 |
| LOW | CSP allows unsafe-inline | app.py:204-205 |
| LOW | Fernet key cached in module-level dict | oauth_models.py:28 |
| LOW | Error message in OTel span may contain user data | app.py:177 |

**Positive highlights:** Database role as single source of truth (never trusts headers), Traefik header stripping, PKCE on all OAuth, SSRF with DNS validation, constant-time API key comparison, ClaudeCode subprocess hardening, AST-based calculator, production secrets validator, CSRF double-submit with HMAC.

---

### D. Component Functionality (#4 -- Sonnet)

**Score: 7.5/10** -- Production-ready with caveats.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | MemoryStore: No retry logic for Qdrant failures | memory.py:112-116 |
| MEDIUM | Dispatcher: Context creation without ownership validation | dispatcher.py:298-311 |
| MEDIUM | UnifiedOrchestrator: Fallback plan on error doesn't preserve error details | unified_orchestrator.py:158-174 |
| MEDIUM | Multiple tools lack timeout configuration | Various |
| MEDIUM | No timeout on LLM generate calls | unified_orchestrator.py:155 |
| MEDIUM | MCP tool run() has no timeout | mcp_loader.py:59 |
| MEDIUM | MemoryStore: Upsert failure silently logged, not reported | memory.py:174-180 |
| MEDIUM | SkillExecutor uses assert in production (stripped with -O) | executor.py:620 |
| MEDIUM | LiteLLM error responses read full body into memory | litellm_client.py:82-84 |
| MEDIUM | LiteLLM global timeout, no per-request override | litellm_client.py:32-33 |
| MEDIUM | MCP client pool not thread-safe during init | mcp_loader.py:143-144 |

**Maturity ratings:** SkillExecutor EXCELLENT, ChunkFilter EXCELLENT, ContextService EXCELLENT, DB Models EXCELLENT, LiteLLM Client HIGH, SkillRegistry HIGH, Dispatcher HIGH.

---

### E. Dead Code (#5 -- Sonnet)

**Grade: A-** -- Excellent code hygiene, minimal dead code.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | 4 unregistered tool classes: SearchCodeBaseTool, GitHubTool, RunPytestTool, RunLinterTool | core/tools/search_code.py, github.py, qa.py |
| MEDIUM | Context7 module fully implemented but never activated (tools not registered, no skills) | modules/context7/ |
| LOW | Only 3 TODO markers in entire codebase | Various |
| LOW | colorama dependency only used by one-time script | scripts/ingest_tibp_wiki.py |

**Positive:** All registered tools referenced by skills, all dependencies used, all agents active, all slash commands active, 0 duplicate configs.

---

### F. Performance (#6 -- Opus)

**2 CRITICAL, 4 HIGH, 7 MEDIUM findings.**

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | WebFetcher cache uses synchronous file I/O, blocks event loop | modules/fetcher/__init__.py:114-124 |
| CRITICAL | Missing composite index on Conversation(platform, platform_id) -- full table scan | core/db/models.py:73-74 |
| HIGH | MCP ping() uses list_tools() -- fetches ALL tool defs just to check health | core/mcp/client.py:431-433 |
| HIGH | inspect.currentframe() on every MemoryStore init without context_id | core/core/memory.py:76-79 |
| HIGH | Double tool registry loading at startup | interfaces/http/app.py:441 + service_factory.py:61 |
| HIGH | WebFetcher cache has no size limit or eviction | modules/fetcher/__init__.py:123-124 |
| MEDIUM | Unbounded _validated_contexts cache in SkillExecutor | core/skills/executor.py:74 |
| MEDIUM | Sync file write in crash log exception handler | interfaces/http/app.py:182 |
| MEDIUM | New httpx.AsyncClient per readiness probe | interfaces/http/app.py:674 |
| MEDIUM | N+1 query in retention cleanup | core/db/retention.py:119-143 |
| MEDIUM | Homey session cache has no TTL | core/tools/homey.py:134 |
| MEDIUM | _NoOpTraceAPI instantiated on every tracing call | core/observability/tracing.py:290+ |
| MEDIUM | WebFetcher rate limiter is synchronous | modules/fetcher/__init__.py:100-108 |

**Positive (18 items):** Shared httpx client (200 connections), shared Qdrant client, proper SQLAlchemy pool (10+20), lru_cache on Settings, orjson for streaming, SSE token batching, MCP negative cache + background eviction, asyncio.sleep(0) yielding.

---

### G. Logging & Observability (#7 -- Sonnet)

**Grade: B-** -- Solid foundation, needs refinement.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | No latency percentiles (p50/p95/p99) | core/diagnostics/service.py:68-151 |
| CRITICAL | No per-conversation diagnosis (messages.trace_id not indexed) | interfaces/http/admin_api.py:335-453 |
| HIGH | Inconsistent log levels across modules | core/core/service.py (multiple) |
| HIGH | 30+ exception handlers without logging (swallowed errors) | Various |
| HIGH | Debug logging is opt-in, no always-on timing metrics | core/debug/logger.py:60-88 |

**Missing tracing on:** DB retention, OAuth token refresh, MCP reconnection, price tracker scraping, workspace sync.

**Positive:** Structured error codes, OTel tracing with file+OTLP exporters, toggle-able debug logging, security event logging, diagnostic API.

---

### H. Testing Gaps (#8 -- Sonnet)

**34% test/source ratio, 2 CRITICAL gaps.**

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | SkillExecutor context ownership validation completely untested | core/skills/executor.py:76-100 |
| CRITICAL | UnifiedOrchestrator plan parsing has 0 dedicated tests | core/routing/unified_orchestrator.py:100-200 |
| HIGH | StepSupervisor decision logic minimal coverage (abort never tested) | core/agents/supervisor_step.py:84-100 |
| HIGH | PlannerAgent sanitization integration untested (is it actually called?) | core/agents/planner.py:78-86 |
| HIGH | MCP client OAuth token refresh untested | core/mcp/client.py:126-150 |
| HIGH | Admin portal role trust validation gaps (header spoofing) | interfaces/http/admin_auth.py |
| MEDIUM | Streaming error doesn't sanitize exception messages | orchestrator/dispatcher.py:438-446 |
| MEDIUM | SkillExecutor tool scoping enforcement untested | core/skills/executor.py:200-250 |
| MEDIUM | No DB connection pool exhaustion tests | core/db/engine.py |
| MEDIUM | Rate limit multi-tenant isolation untested | Various |

**Positive:** CSRF 18 tests, credential encryption 26 tests, dispatcher 943 lines of tests, auth 92% code ratio, semantic testing with LLM-as-judge.

---

### I. Stack Script (#9 -- Sonnet)

**Rating: Good** -- Well-structured, safe subprocess handling.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | Missing timeouts on critical subprocess calls (docker commands can hang) | cli.py:227-534, tooling.py:64-76 |
| HIGH | Import commands (openwebui, n8n) lack confirmation prompts | cli.py:1520-1574, 1410-1473 |
| HIGH | CI/stack pytest parity gap (CI: `-x -n auto`, local: default) | checks.py:357-378 |
| MEDIUM | Duplicate deployment logic (~60 lines) between dev_deploy and deploy | cli.py:654-718, 893-1005 |
| MEDIUM | Inconsistent error message formatting across commands | Various |
| MEDIUM | Branch push protection missing | cli.py:1219-1247 |
| MEDIUM | No deployment history viewing command | cli.py:150-217 |
| MEDIUM | Redundant health checks in dev deploy | cli.py:686-712 |

**Positive:** No shell=True usage, consistent Rich formatting, good test coverage (584 lines), clear separation of concerns, branch protection in deploy.

---

### J. CI/CD Pipeline (#10 -- Sonnet)

**Grade: B+** -- Strong security scanning, needs optimization.

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| HIGH | No automated smoke tests post-deploy (only /health check) | cli.py:978-1005 |
| HIGH | No multi-stage Docker build (larger image, build tools leak) | Dockerfile:15-32 |
| MEDIUM | No Docker layer caching in CI | .github/workflows/ci.yml:107-112 |
| MEDIUM | Trivy rebuilds Docker image instead of reusing CI artifact | .github/workflows/trivy.yml:30-35 |
| MEDIUM | No coverage reporting in CI | .github/workflows/ci.yml:79-81 |
| MEDIUM | pip-audit results not uploaded to Security tab | .github/workflows/ci.yml:157-158 |
| MEDIUM | No test result artifacts | .github/workflows/ci.yml:79-81 |
| MEDIUM | Branch protection not version-controlled | Documentation only |
| MEDIUM | No rollback testing in CI | cli.py:1007-1023 |

**Positive:** Trivy + CodeQL + pip-audit + Gitleaks (defense-in-depth), optimal job parallelization (~5 min total), 90% cache hit rate, quality gate pattern, BuildKit enabled, pre-commit hooks.

---

### K. Agent Config & Architecture Guards (#11 -- Sonnet)

**4 CRITICAL findings around enforcement gaps.**

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | Architecture checks BYPASSED in CI (skip_architecture=True) | stack/cli.py:952 |
| CRITICAL | Orchestrator layer not validated by architecture checker | core/validators/architecture.py:237 |
| CRITICAL | TYPE_CHECKING blocks not analyzed (can hide circular deps) | core/validators/architecture.py |
| CRITICAL | Core/tools importing modules directly (price_tracker) | core/tools/price_tracker.py:66 |
| HIGH | 16 known violations baselined permanently | .architecture-baseline.json |
| HIGH | Admin portal directly imports modules (15 violations) | interfaces/http/app.py:341-344 |
| HIGH | ARCHITECTURE.md says "3-layer", code enforces "4-layer" | docs/ARCHITECTURE.md:1-3 |
| HIGH | TODO comments for violation fixes never addressed | stack/cli.py:952-954 |
| MEDIUM | Dependency matrix in architect.md shows interfaces->modules as allowed (validator flags it) | .claude/agents/architect.md:57-62 |
| MEDIUM | No pre-commit hook for architecture checks | N/A |
| MEDIUM | Slash commands too minimal (no project context) | .claude/commands/*.md |
| MEDIUM | Test acknowledges violations as "temporary" but baseline is permanent | test_architecture_validator.py:279 |

**Positive:** Baseline system is well-designed, agent configs comprehensive, protocol-based DI correctly documented.

---

### L. Refactoring Opportunities (#12 -- Sonnet)

**~3,300 lines of removable/reducible code.**

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | admin_price_tracker.py: 2,623 lines (split into modules) | interfaces/http/admin_price_tracker.py |
| CRITICAL | AgentService: 2,543 lines god class (split into coordinator pattern) | core/core/service.py |
| CRITICAL | Duplicated store/product fetching logic (repeated 3x) | admin_price_tracker.py:200-780 |
| HIGH | UUID validation boilerplate repeated ~40 times (use FastAPI Path validation) | All admin_*.py |
| HIGH | Stack CLI: 1,734 lines single file (split into subcommands) | stack/cli.py |
| HIGH | Error handling anti-pattern repeated ~60 times | All admin_*.py |
| HIGH | Homey tool in-memory session cache (no TTL, not multi-tenant safe) | core/tools/homey.py:134 |
| HIGH | admin_diagnostics.py: 1,510 lines with embedded HTML | interfaces/http/admin_diagnostics.py |
| HIGH | SkillExecutor: 754 lines with mixed concerns | core/skills/executor.py |
| HIGH | Duplicated "next check scheduling" logic | admin_price_tracker.py:605-630 |
| HIGH | Magic numbers in frequency validation | admin_price_tracker.py:509,579 |

---

### M. Configuration Review (#13 -- Sonnet)

**Config health: 61% (38/62 vars actively used).**

**Findings:**
| Sev | Finding | Detail |
|-----|---------|--------|
| HIGH | 12 env vars in .env.template never used (AGENT_SQLITE_STATE_PATH, HOMEY_API_TOKEN, ENABLE_QDRANT, etc.) | Various |
| HIGH | 8 env vars used in code but missing from .env.template (POSTGRES_URL, GITHUB_TOKEN, AGENT_WORKSPACE_BASE, etc.) | Various |
| MEDIUM | Azure DevOps PAT is global (should be per-user credential in DB) | docker-compose.yml:45 |
| MEDIUM | RAG module uses os.getenv() directly instead of Settings (7 variables bypass config.py) | modules/rag/__init__.py:19-24 |
| MEDIUM | LiteLLM config name inconsistency (AGENT_LITELLM_API_BASE vs LITELLM_BASE_URL) | config.py:37 vs docker-compose.yml:40 |

**Positive:** Strong encryption key management, clear API key separation, multi-environment support, OAuth already DB-backed.

---

### N. Admin Portal HTML/JS (#14 -- Sonnet)

**Strong security, weak UX.**

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | No disabled state on form submission -- double-click creates duplicates | admin_credentials.py:219-242 |
| HIGH | 80%+ of fetch calls have no error handling (network errors invisible) | All admin_*.py |
| HIGH | User management is read-only (CRUD endpoints exist but UI only shows list) | admin_users.py:76-93 |
| HIGH | MCP editServer uses setTimeout(300ms) race condition | admin_mcp.html:295-315 |
| HIGH | No pagination on workspace/context lists | admin_workspaces.py:42-46 |
| HIGH | Delete confirmations are generic (no resource impact warnings) | admin_credentials.py:322, admin_mcp.html:375 |
| MEDIUM | Modals lack aria-label, role="dialog" | admin_shared.py:290-326 |
| MEDIUM | Missing skip navigation links | All dashboards |
| MEDIUM | Duplicate CSS across modules (~150 lines) | admin_mcp.html, admin_workspaces.py |
| MEDIUM | Health check auto-refresh never cleared on page unload (memory leak) | admin_portal.py:199-230 |

**Positive:** CSRF auto-injected via fetch wrapper, consistent escapeHtml() everywhere, Entra ID on all endpoints, encrypted credentials, security event logging, good toast notification pattern.

---

### O. Docker & Infrastructure (#15 -- Sonnet)

**3 CRITICAL operational gaps.**

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | Docker socket exposure (read-only but can read all container env vars) | docker-compose.prod.yml:45 |
| CRITICAL | No resource limits (memory/CPU) on any container | All compose files |
| CRITICAL | No log rotation -- unbounded log growth fills disk | All compose files |
| HIGH | Images use mutable tags (:latest, :main-latest) not digests | docker-compose.yml:3,96,111 |
| HIGH | ACME file permissions may break if Traefik switches to non-root | data/letsencrypt/acme.json |
| HIGH | No DB connection pool settings in docker-compose environment | docker-compose.yml:39 |
| HIGH | Traefik access logs enabled without size limits | docker-compose.prod.yml:39 |
| MEDIUM | Agent healthcheck timeout too aggressive (5s, should be 10s with 60s start_period) | docker-compose.yml:66-70 |
| MEDIUM | Postgres healthcheck doesn't verify database exists | docker-compose.yml:90-93 |
| MEDIUM | Qdrant healthcheck uses TCP instead of HTTP /healthz | docker-compose.yml:104-108 |
| MEDIUM | Entrypoint script URL parsing is fragile (sed regex) | entrypoint.sh:11-23 |

**Positive:** Environment isolation (separate project names, volumes, networks, ports), header stripping, TLS 1.2+ with strong ciphers, security headers, non-root container, restart policies, HTTP->HTTPS redirect, Let's Encrypt auto-SSL.

---

### P. Database & Migrations (#16 -- Sonnet)

**2 CRITICAL index issues.**

**Findings:**
| Sev | Finding | Location |
|-----|---------|----------|
| CRITICAL | Missing FK indices: conversations.context_id, sessions.conversation_id, messages.session_id | alembic/versions/4163dd6af66a:36-76 |
| CRITICAL | N+1 query in admin contexts (3 extra queries per context) | admin_contexts.py:188-230 |
| HIGH | Missing composite index for retention query (session_id + created_at) | core/db/retention.py:131-136 |
| HIGH | conversations.metadata column missing server_default in migration | alembic/versions/0255b3157905:25 |
| HIGH | Nullable user_id in unique constraint allows duplicates (PostgreSQL NULL semantics) | 20260118_add_user_id_to_oauth_tokens.py:36-40 |
| HIGH | No admin list endpoint has pagination | admin_contexts.py:188, admin_oauth.py:198 |
| MEDIUM | No data migration to encrypt pre-existing plaintext OAuth tokens | oauth_models.py:87-114 |
| MEDIUM | Initial schema FK constraints lack CASCADE (may have RESTRICT) | alembic/versions/4163dd6af66a:44-47 |
| MEDIUM | Nullable FK business logic undocumented (oauth_tokens.user_id) | oauth_models.py:148-149 |

**Positive:** Linear migration chain (21 migrations, 2 merges resolved), all migrations reversible, consistent CASCADE rules on newer tables, proper eager loading in scheduler, indexed timestamps, UUID primary keys.

---

## 4. Cross-Cutting Themes

### Theme 1: Missing Timeouts
Found across 6 areas: LLM calls, MCP tools, subprocess calls, async operations, DB queries, health checks. No component has consistent timeout enforcement.

### Theme 2: Sync I/O in Async Context
WebFetcher cache, CodeIndexer file reads, crash log writes, model registry reads. The project is async-first but has pockets of blocking I/O.

### Theme 3: Unbounded Growth
Caches without TTL/LRU (validated_contexts, homey sessions, fetcher disk cache), lists without pagination, logs without rotation, Docker without resource limits.

### Theme 4: Error Handling Inconsistency
Admin portal: 80% of fetch calls lack error handling. Backend: 30+ exception handlers swallow errors. Logging: mixed levels and styles. Users often see no feedback on failures.

### Theme 5: Architecture Governance Disabled
Validator exists but is bypassed in CI and deploys. 16 violations baselined permanently. No pre-commit hook. Orchestrator layer not even validated.

---

## 5. Phased Roadmap

### Phase 1: Critical Fixes (Week 1) -- 1-2 days

1. Create Alembic migration for missing FK indices + composite platform index
2. Enable architecture checks in CI (remove skip_architecture=True)
3. Fix non-constant-time OAuth state comparison
4. Add Docker resource limits and log rotation
5. Add confirmation prompts to import commands
6. Fix form double-submission in admin portal

### Phase 2: Security & Performance (Weeks 2-3) -- 3-4 days

7. Fix Cookie Secure flag behind Traefik
8. Add git_clone URL sanitization
9. Convert WebFetcher cache to async I/O
10. Replace MCP ping() with lightweight health check
11. Fix N+1 query in admin contexts
12. Add missing subprocess timeouts to stack CLI
13. Add smoke tests post-deploy

### Phase 3: Testing & Observability (Weeks 3-4) -- 3-5 days

14. Write tests for SkillExecutor context validation
15. Write tests for UnifiedOrchestrator plan parsing
16. Add latency percentiles to diagnostics
17. Index messages.trace_id for per-conversation diagnosis
18. Fix exception handlers that swallow errors (30+)
19. Add coverage reporting to CI

### Phase 4: Refactoring & Config (Weeks 5-8) -- 1-2 weeks

20. Split admin_price_tracker.py (2,623 lines -> modular)
21. Refactor AgentService god class (2,543 lines -> coordinator)
22. Extract UUID validation + error handling decorators
23. Clean up .env.template (remove 12 unused vars, add 8 missing)
24. Multi-stage Docker build
25. Fix admin portal fetch error handling (all modules)

### Phase 5: Architecture Debt (Weeks 9-12) -- 2-3 weeks

26. Add orchestrator validation to architecture checker
27. Fix all 16 baselined violations
28. Migrate Azure DevOps credentials to per-user DB storage
29. Refactor RAG/Fetcher to use Settings (not os.getenv)
30. Split stack CLI into subcommands
31. Add admin portal pagination to all list endpoints
32. Implement Docker socket proxy for Traefik

---

*Report generated by 16 parallel audit agents (3 Opus, 13 Sonnet) on 2026-02-10.*
