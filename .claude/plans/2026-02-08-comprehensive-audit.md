# Comprehensive Platform Audit

**Date:** 2026-02-08
**Branch:** `feat/security-quality-hardening`
**Auditors:** 11 parallel Architect agents (Opus)
**Scope:** Full codebase -- architecture, security, performance, testing, CI/CD, observability, docs, dead code, stack CLI, agent config

---

## 1. Executive Summary

| Area | CRITICAL | HIGH | MEDIUM | LOW | Total |
|------|----------|------|--------|-----|-------|
| A. Architecture | 0 | 2 | 6 | 6 | 14 |
| B. Documentation | 3 | 10 | 15 | 9 | 37 |
| C. Security | 0 | 2 | 7 | 9 | 18 |
| D. Component Functionality | 1 | 4 | 5 | 5 | 15 |
| E. Dead Code | 1 | 8 | 10 | 7 | 26 |
| F. Performance | 1 | 5 | 8 | 4 | 18 |
| G. Logging & Observability | 0 | 4 | 11 | 6 | 21 |
| H. Testing Gaps | 4 | 5 | 5 | 2 | 16 |
| I. Stack CLI | 0 | 4 | 8 | 7 | 19 |
| J. CI/CD Pipeline | 3 | 6 | 8 | 4 | 21 |
| K. Agent Config & Guards | 2 | 6 | 8 | 5 | 21 |
| **TOTAL** | **15** | **56** | **91** | **64** | **226** |

**Overall Grade: B** -- Solid architecture foundation with strong security posture, but significant gaps in testing (6-8% CI coverage), CI/CD (broken scanner, no dependency audit), and documentation drift. The platform is production-usable but needs hardening in the areas identified below.

---

## 2. Top 20 Priority Fixes

Ranked by risk (security exposure, data loss potential, production impact) and effort.

| Rank | ID | Area | Severity | Fix | Effort | Impact |
|------|-----|------|----------|-----|--------|--------|
| 1 | J-C01 | CI/CD | CRITICAL | Rename `trivy.tml` to `trivy.yml` (container scanner never ran) | 30 sec | Restores container vulnerability scanning |
| 2 | H-1 | Testing | CRITICAL | Add `src/*/tests/` dirs to CI testpaths -- 458 tests invisible to CI | 10 min | +458 tests in CI, including security tests (SSRF, CSRF, rate limit) |
| 3 | B-C1 | Docs | CRITICAL | Add `AGENT_DIAGNOSTIC_API_KEY`, `AGENT_ADMIN_API_KEY`, `AGENT_INTERNAL_API_KEY` to `.env.template` | 5 min | Fresh deploys get auth on critical endpoints |
| 4 | C-H1 | Security | HIGH | Add `internal_api_key` to production startup validation in `config.py` (agent API unauthenticated when key unset) | 15 min | Prevents accidental open-auth production |
| 5 | E-3 | Dead Code | CRITICAL | Delete root `config/tools.yaml` (duplicate, stale, never used in production) | 1 min | Eliminates config confusion |
| 6 | J-C12 | CI/CD | CRITICAL | Add `pip-audit` CI job for dependency vulnerability scanning | 30 min | Detects CVEs in 100+ transitive deps |
| 7 | J-C14 | CI/CD | CRITICAL | Add `--cov --cov-fail-under=50` to CI pytest | 15 min | Coverage visibility and enforcement |
| 8 | C-H2 | Security | HIGH | Add `/v1/agent` to OTel span `skip_body` list (request bodies may contain secrets) | 5 min | Prevents secret leakage to span files |
| 9 | F-1 | Performance | CRITICAL | Cache Qdrant `get_collection()` result (called on EVERY request, +5-15ms) | 30 min | Eliminates per-request Qdrant round-trip |
| 10 | K-A1 | Config | CRITICAL | Add architecture validation to CI pipeline | 30 min | Prevents architecture violations from merging |
| 11 | A-1 | Architecture | HIGH | Wrap Azure DevOps SDK calls in `asyncio.to_thread()` (blocks event loop) | 30 min | Prevents concurrent request stalling |
| 12 | F-4 | Performance | HIGH | Cache `Fernet()` instance in `oauth_models.py` (recreated on every encrypt/decrypt) | 15 min | CPU savings on every OAuth token read |
| 13 | K-B1 | Config | HIGH | Fix stale agent names in `.clinerules` (`/architect`->`/plan`, `/builder`->`/build`, `/janitor`->`/ops`) | 10 min | Correct guidance for Claude sessions |
| 14 | I-2.1 | Stack CLI | HIGH | Add confirmation prompt to `down --remove-volumes` and `qdrant restore` | 30 min | Prevents accidental data destruction |
| 15 | I-3.1 | Stack CLI | HIGH | Add timeout support to `run_command` and `_run_cmd` | 1 hr | Prevents CLI from hanging forever |
| 16 | I-10.3 | Stack CLI | HIGH | Add post-deploy health check to production `stack deploy` | 30 min | Detects broken deploys immediately |
| 17 | J-C02 | CI/CD | HIGH | Add `push: branches: [main]` trigger to CI workflow | 5 min | Catches post-merge regressions |
| 18 | F-2 | Performance | HIGH | Add shared `httpx.AsyncClient` to `HomeyTool` (new client per API call, +80-200ms) | 45 min | Major latency reduction for smart home commands |
| 19 | J-C13 | CI/CD | HIGH | Create `.github/dependabot.yml` for automated dependency updates | 15 min | Automated CVE detection in deps |
| 20 | H-3 | Testing | CRITICAL | Create tests for `admin_session.py` JWT (96 LOC, zero tests, protects entire admin portal) | 1 hr | Security-critical auth code covered |

---

## 3. Full Findings by Area

### A. Architecture

**Overall Grade: B+** -- Well-structured 4-layer modular monolith with Protocol-based DI. 14 known layer violations tracked in baseline.

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| A1 | HIGH | `AgentService` god class: 2514 lines, 30+ methods, 5+ responsibility clusters | `core/core/service.py` |
| A2 | HIGH | Azure DevOps tool uses synchronous `msrest` HTTP in async context, blocks event loop | `core/tools/azure_devops.py:10-11` |
| A3 | MEDIUM | `admin_price_tracker.py` bypasses orchestrator layer (6 direct module imports) | `interfaces/http/admin_price_tracker.py:36-38` |
| A4 | MEDIUM | `core/core/` double-nesting creates awkward imports (74 occurrences of `from core.core.`) | `core/core/` directory |
| A5 | MEDIUM | `GitHubTool` HTTP client created but never explicitly closed | `core/tools/github.py` |
| A6 | MEDIUM | `GeminiCLIModel` uses synchronous `subprocess.run()` | `core/models/gemini_cli.py:28-30` |
| A7 | MEDIUM | 2 httpx clients missing constructor-level timeouts (per-request only) | `admin_auth_oauth.py:216`, `oauth_client.py:387` |
| A8 | MEDIUM | `TokenManager` uses concrete class instead of Protocol in providers | `core/providers.py` |

**Positive:** Core layer integrity is perfect (zero upward imports). Zero cross-module imports. Protocol-based DI is textbook quality. Shared layer is truly layer-neutral. Graceful shutdown properly closes all resources.

---

### B. Documentation

**Overall: Documentation drift is significant.** Many paths, function names, and protocol names in docs don't match code.

| # | Severity | Finding | File |
|---|----------|---------|------|
| B1 | CRITICAL | `.env.template` missing 3 security API keys (DIAGNOSTIC, ADMIN, INTERNAL) | `.env.template` |
| B2 | CRITICAL | `docs/ARCHITECTURE.md` claims interfaces CANNOT import modules -- but they do | `docs/ARCHITECTURE.md:37` |
| B3 | CRITICAL | `core/tests/` files import from `interfaces.http.app` (layer violation in tests) | `core/tests/test_app.py:18` |
| B4 | HIGH | CLAUDE.md NAV_ITEMS completely stale (5 listed, 13 actual) | `CLAUDE.md:689-695` |
| B5 | HIGH | References non-existent `admin_dashboard.py` (actual: `admin_portal.py`) | `CLAUDE.md:675` |
| B6 | HIGH | `admin_page_layout()` should be `render_admin_page()` | `CLAUDE.md:704-711` |
| B7 | HIGH | User model in docs: `int` IDs vs actual `UUID`, wrong field names | `docs/ARCHITECTURE.md:59-73` |
| B8 | HIGH | All admin endpoint paths documented as `/admin/` (actual: `/platformadmin/`) | `docs/ADMIN_API.md` |
| B9 | HIGH | Executor context injection example is outdated | `CLAUDE.md:475-486` |
| B10 | HIGH | DI wiring documented in `core/core/app.py` (actual: `interfaces/http/app.py`) | `docs/ARCHITECTURE.md:280` |
| B11 | HIGH | Protocol names in docs don't exist (`MemoryProtocol`, `LLMProtocol`, `ToolProtocol`) | `docs/ARCHITECTURE.md:255-260` |
| B12 | HIGH | `docs/SKILLS_FORMAT.md` entire frontmatter schema is wrong | `docs/SKILLS_FORMAT.md:13-18` |
| B13 | HIGH | CLAUDE.md skill directory listing missing 5 skills and 7 tool files | `CLAUDE.md:384-414` |

**Positive:** Protocol-based DI, StepOutcome system, Fernet encryption, quality gate, and multi-tenant architecture are accurately documented.

---

### C. Security

**Overall: Strong security posture with defense-in-depth.** Two HIGH items need attention.

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| C1 | HIGH | Agent API completely unauthenticated when `AGENT_INTERNAL_API_KEY` env var is unset (warning logged but bypassed) | `app.py:82-89` |
| C2 | HIGH | Request bodies captured in OTel spans may contain secrets from `/v1/agent` | `app.py:282-288` |
| C3 | MEDIUM | CSP allows `'unsafe-inline'` for scripts and styles (weakens XSS protection) | `app.py:201-208` |
| C4 | MEDIUM | Differentiated rate limits defined but not actually applied (only global 60/min active) | `rate_limit.py:39-41` |
| C5 | MEDIUM | Admin workspace git clone URL not validated for scheme (could be `file://`) | `admin_workspaces.py:403-406` |
| C6 | MEDIUM | Auto-provisioning of any Entra ID tenant user on login | `admin_auth_oauth.py:300-308` |
| C7 | MEDIUM | ID token decoded without signature verification (`verify_signature: False`) | `admin_auth_oauth.py:258` |
| C8 | MEDIUM | `render_admin_page` `content` parameter inserted raw into HTML | `admin_shared.py:614-621` |
| C9 | MEDIUM | Exception details in OTel spans could contain sensitive data | `app.py:175-176` |

**Positive:** Fernet encryption at rest, CSRF double-submit pattern, no `shell=True` anywhere, constant-time key comparison, comprehensive SSRF protection, PKCE for OAuth, structured security event logging, Claude Code dangerous pattern blocking.

---

### D. Component Functionality

**Maturity ratings:**

| Component | Maturity | Key Issue |
|-----------|----------|-----------|
| Orchestrator (Dispatcher) | MEDIUM | Tight coupling to AgentService, two conversation creation paths |
| Planner | HIGH | Dual planning paths (PlannerAgent + UnifiedOrchestrator) |
| Skill System | EXCELLENT | Scoped tool access, HITL, dedup, context ownership validation |
| Tools (14 registered) | MEDIUM-HIGH | Azure DevOps blocks event loop, Homey creates per-request clients |
| LiteLLM Client | HIGH | No automatic retry on transient HTTP errors |
| RAG Module | LOW | Not integrated into main pipeline, no tests, no timeouts |
| MCP Client | HIGH | Multi-transport, auto-reconnect, health monitoring |
| Conversation Management | HIGH | Full lifecycle, multi-tenant, HITL state persistence |
| SSE Streaming | HIGH | Token batching, verbosity levels, noise filtering |
| Step Supervisor | HIGH | 4-level outcomes, lenient defaults, retry escalation |

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| D1 | CRITICAL | RAG module Qdrant client created without timeout (can hang indefinitely) | `modules/rag/__init__.py:37` |
| D2 | HIGH | Azure DevOps synchronous SDK blocks event loop in async context | `core/tools/azure_devops.py:10-11` |
| D3 | HIGH | RAG module not integrated into agent pipeline (`MemoryStore` used instead) | `modules/rag/__init__.py` |
| D4 | HIGH | RAG module silent error swallowing (bare `except Exception`, returns empty) | `modules/rag/__init__.py:159,221` |
| D5 | HIGH | Homey tool creates new `httpx.AsyncClient` per HTTP call (4 calls per command) | `core/tools/homey.py:177,203,220,286` |

---

### E. Dead Code

**~800 lines of dead code removable across ~15 files.**

| # | Severity | Finding | File |
|---|----------|---------|------|
| E1 | CRITICAL | Two `tools.yaml` files with DIFFERENT content (root vs service-level) | `config/tools.yaml` vs `services/agent/config/tools.yaml` |
| E2 | HIGH | `modules/context7/` entire directory dead (migrated to MCP pool) | `modules/context7/` |
| E3 | HIGH | `core/models/gemini_cli.py` abandoned experiment, never imported | `core/models/gemini_cli.py` |
| E4 | HIGH | 3 unregistered tool classes: `SearchCodeBaseTool`, `OAuthAuthorizeTool`, `RunPytestTool`/`RunLinterTool` | `core/tools/search_code.py`, `oauth_authorize.py`, `qa.py` |
| E5 | HIGH | `set_price_tracker()` provider never called in startup -- price tracker tool always fails via chat | `core/providers.py:128-138` |
| E6 | HIGH | `core/agents/response_agent.py` re-exported but never used in production | `core/agents/response_agent.py` |
| E7 | MEDIUM | `interfaces/protocols.py` dead Protocol definitions | `interfaces/protocols.py` |
| E8 | MEDIUM | 4 orphaned scripts: `check_qdrant_api.py`, `manual_search.py`, `test_unified_orchestrator.py`, `phase5_demo.py` | Various |
| E9 | MEDIUM | 2 unused dev dependencies: `respx`, `aiosqlite` | `pyproject.toml` |
| E10 | MEDIUM | Telegram adapter module exists but never registered in app.py | `interfaces/telegram/` |

---

### F. Performance

**Most impactful: per-request Qdrant check (+5-15ms) and Homey per-call client creation (+80-200ms).**

| # | Severity | Finding | File:Line | Impact |
|---|----------|---------|-----------|--------|
| F1 | CRITICAL | `ainit()` calls `get_collection()` on EVERY request (unnecessary Qdrant round-trip) | `core/core/memory.py:84-99` | +5-15ms/req |
| F2 | HIGH | Homey tool creates new `httpx.AsyncClient` per API call | `core/tools/homey.py:177,203,220,286` | +80-200ms/cmd |
| F3 | HIGH | WebFetcher uses sync file I/O for cache in async code path | `modules/fetcher/__init__.py:114-124` | Blocks event loop 1-5ms |
| F4 | HIGH | Fernet instance recreated on every encrypt/decrypt (vs CredentialService which caches it) | `core/db/oauth_models.py:63,85` | CPU per OAuth op |
| F5 | HIGH | Tool registry cloned on every request even when unnecessary | `core/core/service_factory.py:99` | Memory alloc/req |
| F6 | HIGH | N+1 query in weekly price summary (per-product DB query in loop) | `modules/price_tracker/scheduler.py:397-406` | O(N) queries |
| F7 | MEDIUM | Ephemeral httpx clients in OAuth flow, OAuth discovery, readiness probe | Various | +10-30ms each |
| F8 | MEDIUM | Sync `open()` in global exception handler blocks event loop | `app.py:181` | Blocks on errors |
| F9 | MEDIUM | Unbounded `_validated_contexts` cache in SkillExecutor | `core/skills/executor.py:74` | Memory (slow) |
| F10 | MEDIUM | File-based fetch cache grows without eviction | `modules/fetcher/__init__.py:76-78` | Disk space |
| F11 | MEDIUM | Missing composite index on `Conversation(platform, platform_id)` | `core/db/models.py:73-74` | Slow lookups |

**Positive:** Shared Qdrant client, pooled LiteLLM httpx client, background memory persistence, MCP negative cache, proper DB connection pooling, batched span export.

---

### G. Logging & Observability

**Well-architected foundation** with 4 complementary systems (OTel, JSON logs, debug logging, security events). Key gaps in latency tracking and trace correlation.

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| G1 | HIGH | UnifiedOrchestrator has NO OpenTelemetry span (invisible routing decisions) | `core/routing/unified_orchestrator.py:116` |
| G2 | HIGH | IntentClassifier has NO span (hidden latency contributor) | `core/routing/intent.py` |
| G3 | HIGH | No P50/P95/P99 latency percentiles anywhere in the platform | Platform-wide |
| G4 | HIGH | No per-conversation trace correlation API endpoint | Missing endpoint |
| G5 | MEDIUM | Memory operations (search/add) have no dedicated spans | `core/core/memory.py` |
| G6 | MEDIUM | DB history loading has no span (significant for large conversations) | `service.py:170` |
| G7 | MEDIUM | Debug stats not filterable by conversation_id | `admin_api.py` |
| G8 | MEDIUM | Planner input sanitization logged at DEBUG (should be INFO -- security event) | `planner.py:49` |
| G9 | MEDIUM | `RATE_LIMIT_EXCEEDED` and `SUSPICIOUS_ACTIVITY` security events defined but never emitted | `security_logger.py` |
| G10 | MEDIUM | Crash log uses sync `open()` in async handler | `app.py:162` |
| G11 | MEDIUM | Error codes not used in main request pipeline (only in diagnostics) | `error_codes.py` |

**Positive:** Graceful OTel degradation (NoOpSpan pattern), dual-write debug logging, security event correlation with trace context, structured error codes, comprehensive diagnostics service (15 health checks), async-batched file span exporter, tool arg sanitization.

---

### H. Testing Gaps

**Most alarming area.** Estimated CI coverage: 6-8%. 458 tests (56%) never run in CI.

| # | Severity | Finding | Details |
|---|----------|---------|---------|
| H1 | CRITICAL | 458 tests exist outside CI testpaths and NEVER run in CI | Includes SSRF, CSRF, rate limit, security logger tests |
| H2 | CRITICAL | `admin_session.py` JWT auth has ZERO tests (protects entire admin portal) | 96 LOC, no coverage |
| H3 | CRITICAL | `Dispatcher` (central routing, 445 LOC) has ZERO tests | Primary request entry point |
| H4 | CRITICAL | `token_manager.py` (OAuth token management, 121 LOC) has ZERO tests | Manages all provider tokens |
| H5 | HIGH | Admin portal (~11K LOC, 15 modules) has ZERO tests in CI | Complete testing blind spot |
| H6 | HIGH | 10+ tool implementations completely untested (incl. security-sensitive `claude_code.py`) | ~2K LOC |
| H7 | HIGH | `db/retention.py` performs destructive DELETEs with ZERO tests | 178 LOC, data loss risk |
| H8 | HIGH | MCP Manager (299 LOC) has ZERO tests | Connection management |
| H9 | HIGH | `SkillExecutor._validate_context_ownership` (privilege escalation prevention) untested | Security-critical |
| H10 | MEDIUM | Duplicate CSRF test files (one in CI, one not) | Maintenance burden |
| H11 | MEDIUM | No test factories or shared conftest for common fixtures | Each test reinvents setup |

---

### I. Stack CLI

**Functional but missing safety guards for destructive operations.**

| # | Severity | Finding | File:Line |
|---|----------|---------|-----------|
| I1 | HIGH | `down --remove-volumes` destroys all persistent data without confirmation | `cli.py:366-395` |
| I2 | HIGH | `qdrant restore` runs `rm -rf` without confirmation | `qdrant.py:130` |
| I3 | HIGH | `run_command` has no timeout parameter (subprocesses can hang forever) | `tooling.py:52-74` |
| I4 | HIGH | No post-deploy health check for production `stack deploy` | `cli.py:860-874` |
| I5 | MEDIUM | `--prod` and `--dev` flags not mutually exclusive in db commands | `cli.py:1463-1497` |
| I6 | MEDIUM | `.env` values override OS env vars (reverse of convention) | `utils.py:24-38` |
| I7 | MEDIUM | REPO_ROOT computed 3 different ways across files | `cli.py`, `qdrant.py`, `utils.py`, `checks.py` |
| I8 | MEDIUM | Pytest flags differ between local and CI (`-x -n auto` only in CI) | `checks.py:371` vs `ci.yml:66` |
| I9 | MEDIUM | Architecture checks skipped in deploy (`skip_architecture=True` TODO) | `cli.py:842` |
| I10 | MEDIUM | Dead code: `ComposeError` catch path unreachable | `compose.py:85-86` |

**Positive:** No `shell=True` usage, `db rollback` has proper confirmation prompt, qdrant HTTP client has timeout.

---

### J. CI/CD Pipeline

**Solid foundation but critical security scanning gaps.**

| # | Severity | Finding | File |
|---|----------|---------|------|
| J1 | CRITICAL | Trivy container scanner disabled by `.tml` typo (NEVER executed) | `.github/workflows/trivy.tml` |
| J2 | CRITICAL | No dependency vulnerability scanning (pip-audit/safety) | Missing |
| J3 | CRITICAL | No test coverage reporting or enforcement in CI | `ci.yml:66` |
| J4 | HIGH | CI only triggers on `pull_request`, not on push to main | `ci.yml:3-4` |
| J5 | HIGH | Trivy action pinned to `@master` (supply chain risk) | `trivy.tml:44` |
| J6 | HIGH | No Dependabot/Renovate for automated dependency updates | Missing `.github/dependabot.yml` |
| J7 | HIGH | No CD pipeline -- entirely manual SSH + `stack deploy` | No CD workflow |
| J8 | HIGH | No post-deploy health check in production deploy | `cli.py:862-874` |
| J9 | HIGH | No scheduled dependency vulnerability scan | Missing |
| J10 | MEDIUM | Triplicated setup steps across 3 CI jobs (no reusable workflow) | `ci.yml:14-65` |
| J11 | MEDIUM | No Docker layer caching in CI | `trivy.tml:38-41` |
| J12 | MEDIUM | Architecture validator not in CI | `ci.yml` |
| J13 | MEDIUM | Services use mutable `:latest` tags in docker-compose.yml | `docker-compose.yml` |
| J14 | MEDIUM | PR template references outdated commands and directories | `.github/pull_request_template.md` |

**Positive:** Quality gate pattern properly implemented, no secrets in CI, lock file committed, pip cache enabled, CodeQL SAST configured with `security-extended` queries.

---

### K. Agent Config & Architecture Guards

**Validator is well-implemented but not enforced in CI.**

| # | Severity | Finding | File |
|---|----------|---------|------|
| K1 | CRITICAL | Architecture validator NOT enforced in CI -- violations can be merged freely | `ci.yml` (missing) |
| K2 | CRITICAL | Production deploy skips architecture validation (`skip_architecture=True`) | `cli.py:842` |
| K3 | HIGH | `.clinerules` uses stale agent names (`/architect`, `/builder`, `/janitor`) | `.clinerules:15-22` |
| K4 | HIGH | Stale reference to non-existent `.claude/PRIMER.md` | `.clinerules:29` |
| K5 | HIGH | Stale diagnostics API paths in `.clinerules` (`/diagnostics/` vs `/platformadmin/`) | `.clinerules:111-116` |
| K6 | HIGH | `architect.md` lists protocols that don't exist (`ILLMProtocol`, `MemoryProtocol`, `ToolProtocol`) | `.claude/agents/architect.md:414-421` |
| K7 | MEDIUM | Missing orchestrator layer validation in validator | `core/validators/architecture.py` |
| K8 | MEDIUM | `shared/` layer not validated for upward imports | `core/validators/architecture.py` |
| K9 | MEDIUM | Stale baseline entry for already-fixed `price_tracker.py` violation | `.architecture-baseline.json:2` |
| K10 | MEDIUM | Inconsistent diagnostics paths across CLAUDE.md, .clinerules, engineer.md | Multiple files |

**Positive:** Module isolation is perfect, zero relative imports, TYPE_CHECKING used correctly, baseline mechanism is sound, agent markdown files are comprehensive.

---

## 4. Cross-Cutting Themes

### Theme 1: Security Tests Exist But Don't Run in CI
The most alarming cross-cutting finding: SSRF protection tests, CSRF tests, rate limit tests, security logger tests, and admin auth tests are **all written** but live outside CI testpaths. The platform has better security testing than CI reveals -- fixing testpaths is the single highest-ROI action.

### Theme 2: Azure DevOps Synchronous SDK
Found independently by Architecture (A2), Component Functionality (D2), and Performance (F-related). The `azure-devops` Python SDK uses blocking HTTP under the hood. When called from async context, it blocks the entire event loop. This is the most impactful single-file fix.

### Theme 3: Documentation Drift Is Systemic
Documentation audit (B), Agent Config audit (K), and Dead Code audit (E) all found stale references. Protocol names, file paths, function names, admin endpoint paths, and tool listings are all out of date across CLAUDE.md, ARCHITECTURE.md, ADMIN_API.md, SKILLS_FORMAT.md, .clinerules, and agent configs. A systematic documentation refresh is needed.

### Theme 4: Dead Code Accumulation
Dead Code (E) and Component Functionality (D) both flagged the RAG module as unused. Context7 module, GeminiCLI model, 3 unregistered tools, and duplicate config files all indicate features were migrated or abandoned without cleanup. ~800 lines of dead code.

### Theme 5: Per-Request Overhead Is Avoidable
Performance (F) and Component Functionality (D) both identified per-request patterns that should be cached: Qdrant collection verification, Fernet instance creation, tool registry cloning, and Homey HTTP client creation. Combined, these add 20-200ms of avoidable latency per request.

### Theme 6: Inconsistent Confirmation Prompts
Stack CLI (I) found destructive commands with no confirmation (`down --remove-volumes`, `qdrant restore`) alongside commands WITH confirmation (`db rollback`). The pattern exists but is inconsistently applied.

### Theme 7: CI/Stack Parity Gaps
Both CI/CD (J) and Stack CLI (I) flagged differences between `stack check` and CI: architecture validation runs locally but not in CI; pytest flags differ; no post-deploy health checks in production.

---

## 5. Phased Roadmap

### Phase 1: Critical Security & CI Fixes (1-2 days)

**Goal:** Close the most dangerous gaps with minimal effort.

- [ ] Rename `trivy.tml` to `trivy.yml` (J1)
- [ ] Add missing API keys to `.env.template` (B1)
- [ ] Add `internal_api_key` to production startup validation (C1)
- [ ] Add `/v1/agent` to OTel span `skip_body` list (C2)
- [ ] Add `src/*/tests/` directories to CI testpaths (H1)
- [ ] Add `pip-audit` CI job (J2)
- [ ] Add `--cov --cov-fail-under=50` to CI pytest (J3)
- [ ] Add `push: branches: [main]` trigger (J4)
- [ ] Delete root `config/tools.yaml` (E1)

### Phase 2: Performance & Safety Quick Wins (3-5 days)

**Goal:** Fix the highest-impact performance issues and safety gaps.

- [ ] Cache Qdrant collection verification (F1)
- [ ] Cache Fernet instance in `oauth_models.py` (F4)
- [ ] Wrap Azure DevOps calls in `asyncio.to_thread()` (A2/D2)
- [ ] Add shared httpx client to HomeyTool (F2)
- [ ] Add confirmation prompts to destructive stack commands (I1, I2)
- [ ] Add timeout support to `run_command` (I3)
- [ ] Add post-deploy health check to `stack deploy` (I4/J8)
- [ ] Add architecture validation to CI (K1)
- [ ] Create `.github/dependabot.yml` (J6)
- [ ] Pin Trivy action to specific version (J5)

### Phase 3: Testing & Documentation (1-2 weeks)

**Goal:** Raise CI test coverage and fix documentation drift.

- [ ] Create tests for `admin_session.py` JWT (H2)
- [ ] Create tests for `token_manager.py` (H4)
- [ ] Create tests for `Dispatcher` (H3)
- [ ] Create tests for `db/retention.py` (H7)
- [ ] Add security header tests for app.py middleware
- [ ] Add `SkillExecutor._validate_context_ownership` tests (H9)
- [ ] Fix `.clinerules` stale references (K3, K4, K5)
- [ ] Fix CLAUDE.md stale NAV_ITEMS, function names, paths (B4-B13)
- [ ] Fix `docs/ARCHITECTURE.md` protocol names and model types (B7, B11)
- [ ] Fix `docs/SKILLS_FORMAT.md` frontmatter schema (B12)
- [ ] Update admin endpoint paths `/admin/` -> `/platformadmin/` (B8)

### Phase 4: Dead Code Cleanup & Architectural Improvements (2-4 weeks)

**Goal:** Remove dead code, resolve architectural debt.

- [ ] Remove `modules/context7/` directory (E2)
- [ ] Remove dead tools: `search_code.py`, `oauth_authorize.py`, `qa.py` (E4)
- [ ] Remove `core/models/gemini_cli.py`, `core/agents/response_agent.py` (E3, E6)
- [ ] Remove orphaned scripts (E8)
- [ ] Wire `set_price_tracker()` in app.py or remove provider (E5)
- [ ] Decide fate of Telegram adapter (E10)
- [ ] Apply differentiated rate limits via SlowAPI decorators (C4)
- [ ] Add orchestrator layer validation to architecture validator (K7)
- [ ] Wrap WebFetcher cache I/O in `asyncio.to_thread()` (F3)
- [ ] Add composite index on `Conversation(platform, platform_id)` (F11)

### Phase 5: Observability & Polish (ongoing)

**Goal:** Complete the observability stack and remaining improvements.

- [ ] Add OTel spans to UnifiedOrchestrator and IntentClassifier (G1, G2)
- [ ] Add P50/P95/P99 latency percentiles to diagnostic API (G3)
- [ ] Add per-conversation trace correlation endpoint (G4)
- [ ] Add memory operation spans (G5)
- [ ] Begin `AgentService` decomposition (A1)
- [ ] Migrate CSP to nonce-based inline scripts (C3)
- [ ] Add ID token signature verification for Entra ID (C7)
- [ ] Create CD pipeline for automated deployment (J7)
- [ ] Build shared test infrastructure (factories, fixtures, conftest) (H11)
- [ ] Admin portal test suite (H5)
