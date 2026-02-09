Launch 16 parallel architect agents (Opus) to perform a comprehensive platform audit. Each agent independently explores the full codebase and reports findings.

Spawn ALL of these agents simultaneously using the Task tool with subagent_type="architect":

1. **Architecture** -- Validate the 4-layer modular monolith (interfaces -> orchestrator -> modules -> core). Check for layer violations, god classes, circular imports, provider pattern consistency, connection lifecycle issues, and undocumented layers. Report severity (critical/high/medium/low) with file:line references.

2. **Documentation vs Code** -- Compare all docs (CLAUDE.md, docs/*.md, README, skill frontmatter, inline docstrings) against actual code behavior. Find: stale references, wrong paths, outdated protocol names, documented features that no longer exist, existing features missing from docs, config values referencing removed components. For every mismatch, state what the docs say and what the code actually does.

3. **Security** -- The agent and admin portal are externally exposed via Traefik. Audit: authentication on all endpoints, CSP/CORS/CSRF headers, input sanitization (XSS, SSRF, injection), credential/token storage encryption, rate limiting, subprocess safety, prompt injection defenses, and secrets in logs/telemetry. List positive findings too.

4. **Component Functionality** -- Assess maturity (LOW/MEDIUM/HIGH/EXCELLENT) of each component: orchestrator, planner, skill system, each tool, LiteLLM client, RAG module, MCP client, conversation management, SSE streaming. For each: strengths, gaps, and runtime bugs.

5. **Dead Code** -- Find unregistered tools, unused modules, orphaned scripts, duplicate configs, unused dependencies in pyproject.toml, deprecated settings still in code, tools registered but referenced by zero skills.

6. **Performance** -- Check startup time, per-request overhead, connection reuse (HTTP clients, DB, Qdrant, MCP). Find resource leaks (clients created but never closed), missing indices, unbounded caches, sync I/O blocking async event loop.

7. **Logging & Observability** -- Map the current observability stack (OTel spans, debug logs, security events, diagnostic API, admin dashboard). Find gaps: missing trace spans, unqueryable data, debug-only logging that should be always-on, missing per-conversation diagnosis, latency percentile gaps.

8. **Testing Gaps** -- Estimate coverage. Find untested security-critical code (auth, CSRF, input validation), untested core paths (planner, dispatcher, supervisor), CI testpath misconfigurations, missing test infrastructure (factories, fixtures), and gaps in regression/integration/semantic test categories.

9. **Stack Script** -- Review the stack CLI (Typer+Rich). Check: subprocess safety, data-destructive commands without confirmation, duplicate logic, missing timeouts, CI/stack parity, error handling, and UX consistency.

10. **CI/CD Pipeline** -- Review GitHub Actions workflow. Check: build optimization (BuildKit, caching, layer order), missing security scanning (dependency audit, container scan), test coverage reporting, deployment verification, branch protection gaps.

11. **Agent Config & Architecture Guards** -- Review .claude/agents/*.md, .clinerules, CLAUDE.md, and the architecture validator. Check: validator enforcement (warn-only vs fail), CI integration, stale references, dependency matrix accuracy, missing validation rules (upward imports, orchestrator layer, TYPE_CHECKING blocks).

12. **Refactoring Opportunities** -- Identify code that should be simplified or restructured. Look for: overly complex functions (>80 lines), duplicated logic across files, deeply nested conditionals, poor separation of concerns, inconsistent patterns between similar modules, large files that should be split (>500 lines of HTML in Python, >1000 lines total), hard-coded values that should be configurable, and tight coupling that hinders testability. Prioritize by impact: what changes would most improve maintainability?

13. **Configuration Review** -- Audit the full configuration surface: (a) Compare .env.template against actual .env usage in code -- find vars in .env.template not used anywhere, vars used in code but missing from .env.template, and vars with stale default values. (b) Evaluate what config could be simplified or consolidated (redundant settings, settings that could have sensible defaults). (c) Identify configuration that would benefit from moving to per-user/context storage in the database instead of .env (user-specific API keys, per-tenant settings, feature flags). List each finding with the relevant env var name and where it is consumed.

14. **Admin Portal HTML/JS Review** -- Audit all admin portal web interfaces in interfaces/http/admin_*.py and templates/. For each module check: (a) Broken functionality -- JS functions that swallow errors, fetch calls without response checking, forms that fail silently, missing CSRF tokens on mutations. (b) Missing functionality -- UI elements that reference unimplemented features, dead buttons, incomplete CRUD (e.g., create works but edit doesn't). (c) Standards compliance -- inline JS that should use event listeners, missing escapeHtml on user data (XSS), accessibility issues (missing labels, no keyboard nav), inconsistent styling patterns between modules, hardcoded URLs, missing loading states. (d) UX gaps -- no success/error feedback, no confirmation on destructive actions, missing pagination on lists. Report file:line for each finding.

15. **Docker & Infrastructure** -- Audit all Docker Compose files (docker-compose.yml, docker-compose.dev.yml, docker-compose.prod.yml, docker-compose.bind.yml), the Dockerfile, Traefik config, and entrypoint scripts. Check: (a) Environment isolation -- shared volumes/networks between dev and prod that could cause data leakage or conflicts, port collisions when both environments run simultaneously. (b) Traefik configuration -- TLS settings, middleware ordering, header stripping completeness, certificate resolver config, router priority conflicts between services. (c) Container health -- healthcheck commands that match actual service endpoints, appropriate intervals/timeouts/retries, dependency ordering (depends_on with conditions). (d) Security -- Docker socket exposure, images pinned to digests vs tags, secrets passed via env vs files, unnecessary capabilities, containers running as root. (e) Operational gaps -- missing restart policies, no resource limits, no log rotation, volume backup strategy, image pruning.

16. **Database & Migrations** -- Audit Alembic migrations and SQLAlchemy models. Check: (a) Schema consistency -- compare all model definitions in src/ against the actual migration chain to find models missing migrations, columns added in code but not migrated, type mismatches between model and migration. (b) Migration health -- linear migration chain (no forks), all migrations reversible (downgrade works), no data-destructive operations without explicit confirmation, proper use of batch operations for SQLite compatibility. (c) Index coverage -- foreign keys without indices, columns used in WHERE/ORDER BY without indices, composite indices that could replace multiple single-column indices. (d) Relationship integrity -- cascade rules (ON DELETE), orphan handling, nullable foreign keys that should be required, missing unique constraints. (e) Query patterns -- N+1 queries in endpoints (eager loading missing), unbounded SELECT without LIMIT, missing pagination on list endpoints.

$ARGUMENTS

## Instructions for each agent

- Read actual source files -- do not guess
- Report file:line references for every finding
- Categorize: CRITICAL / HIGH / MEDIUM / LOW
- Include positive findings (what is already well-done)
- Focus on security and quality -- skip nice-to-have suggestions that border on bloat
- Output structured markdown with tables where appropriate

## After all agents complete

Consolidate all findings into `.claude/plans/YYYY-MM-DD-comprehensive-audit.md` with:
1. Executive summary table (area x severity counts)
2. Top 20 priority fixes ranked by risk and effort
3. Full findings per area (A-P sections)
4. Cross-cutting themes found across multiple analyses
5. Phased roadmap (critical first, polish last)

Use subagent_type="architect" for all 16 agents. Each agent should be launched with run_in_background=true for maximum parallelism.
