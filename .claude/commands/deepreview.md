Launch a comprehensive platform audit using 4 parallel agents: Gemini CLI handles all pattern-matching and checklist-driven domains in one coherent 1M-token pass; Opus handles the 3 areas requiring deep reasoning.

Spawn ALL 4 agents simultaneously with run_in_background=true:

---

**Agent 1: Gemini Analyst** [subagent_type="gemini-analyst"]

Run from the project root. Invoke as: `gemini -m gemini-3.1-pro-preview --yolo -p "PROMPT"`. Pass this prompt verbatim:

```
You are auditing the ai-agent-platform codebase. Start by reading files systematically using your file tools:
1. Read all Python files under services/agent/src/core/ (agents/, tools/, skills/, runtime/, auth/, db/, observability/)
2. Read all Python files under services/agent/src/interfaces/ (http/admin_*.py, http/templates/*.html, telegram/, scheduler/)
3. Read all skill markdown files under skills/
4. Read services/agent/config/tools.yaml, pyproject.toml, .env.template
5. Read all GitHub Actions workflow files under .github/workflows/
6. Read all Docker Compose files (docker-compose*.yml) and Dockerfile
7. Read alembic/versions/ migrations

After reading, produce a structured markdown report for ALL of the following domains. For every finding include file:line references and severity (CRITICAL / HIGH / MEDIUM / LOW). Include positive findings (things done well) alongside issues.

## Domain A: Documentation vs Code
Compare all docs (CLAUDE.md, docs/*.md, README, skill frontmatter, inline docstrings) against actual code behavior. Find: stale references, wrong paths, outdated protocol names, documented features that no longer exist, existing features missing from docs, config values referencing removed components. For every mismatch, state what the docs say vs what the code actually does.

## Domain B: Component Functionality
Assess maturity (LOW/MEDIUM/HIGH/EXCELLENT) of each component: orchestrator, planner, skill system, each tool, LiteLLM client, MCP client, conversation management, SSE streaming. For each: strengths, gaps, and runtime bugs.

## Domain C: Dead Code
Find unregistered tools, unused modules, orphaned scripts, duplicate configs, unused dependencies in pyproject.toml, deprecated settings still in code, tools registered but referenced by zero skills.

## Domain D: Logging & Observability
Map the current observability stack (OTel spans, debug logs, security events, diagnostic API, admin dashboard). Find gaps: missing trace spans, unqueryable data, debug-only logging that should be always-on, missing per-conversation diagnosis, latency percentile gaps.

## Domain E: Testing Gaps
Estimate coverage. Find untested security-critical code (auth, CSRF, input validation), untested core paths (planner, dispatcher, supervisor), CI testpath misconfigurations, missing test infrastructure (factories, fixtures), and gaps in regression/integration/semantic test categories.

## Domain F: Stack Script
Review the stack CLI (Typer+Rich). Check: subprocess safety, data-destructive commands without confirmation, duplicate logic, missing timeouts, CI/stack parity, error handling, and UX consistency.

## Domain G: CI/CD Pipeline
Review GitHub Actions workflow. Check: build optimization (BuildKit, caching, layer order), missing security scanning (dependency audit, container scan), test coverage reporting, deployment verification, branch protection gaps.

## Domain H: Agent Config & Architecture Guards
Review .claude/agents/*.md, CLAUDE.md, and the architecture validator. Check: validator enforcement (warn-only vs fail), CI integration, stale references, dependency matrix accuracy, missing validation rules (upward imports, orchestrator layer, TYPE_CHECKING blocks).

## Domain I: Refactoring Opportunities
Find: overly complex functions (>80 lines), duplicated logic across files, deeply nested conditionals, poor separation of concerns, inconsistent patterns between similar modules, large files that should be split (>500 lines of HTML in Python, >1000 lines total), hard-coded values that should be configurable, tight coupling that hinders testability. Prioritize by impact.

## Domain J: Configuration Review
Audit the full configuration surface: (a) Compare .env.template against actual .env usage in code -- find vars in .env.template not used anywhere, vars used in code but missing from .env.template, stale default values. (b) Config that could be simplified or consolidated. (c) Config that would benefit from moving to per-user/context DB storage instead of .env.

## Domain K: Admin Portal HTML/JS
Audit all admin portal web interfaces in interfaces/http/admin_*.py and templates/. For each module: (a) Broken functionality -- JS that swallows errors, fetch calls without response checking, forms that fail silently, missing CSRF tokens on mutations. (b) Missing functionality -- dead buttons, incomplete CRUD. (c) Standards compliance -- inline JS, missing escapeHtml on user data (XSS), accessibility, inconsistent styling, hardcoded URLs, missing loading states. (d) UX gaps -- no success/error feedback, no confirmation on destructive actions, missing pagination.

## Domain L: Docker & Infrastructure
Audit all Docker Compose files, Dockerfile, Traefik config, entrypoint scripts. Check: (a) Environment isolation -- shared volumes/networks between dev and prod. (b) Traefik -- TLS settings, middleware ordering, header stripping, router priority conflicts. (c) Container health -- healthcheck commands match actual endpoints, dependency ordering. (d) Security -- Docker socket exposure, image pinning, secrets via env vs files, containers running as root. (e) Operational gaps -- restart policies, resource limits, log rotation.

## Domain M: Database & Migrations
Audit Alembic migrations and SQLAlchemy models. Check: (a) Schema consistency -- models missing migrations, columns added in code but not migrated, type mismatches. (b) Migration health -- linear chain, reversible downgrades, no unguarded destructive ops. (c) Index coverage -- foreign keys without indices, unindexed WHERE/ORDER BY columns. (d) Relationship integrity -- cascade rules, orphan handling, missing unique constraints. (e) Query patterns -- N+1 queries, unbounded SELECT without LIMIT, missing pagination.

---

Output a single structured markdown document with all 13 domains (A-M). Use severity counts per domain in a summary table at the top.
```

Save Gemini's full output to `.claude/gemini-audit-output.md`.

---

**Agent 2: Architecture** [OPUS] [subagent_type="architect"]

Validate the 4-layer modular monolith (interfaces -> orchestrator -> modules -> core). Check for layer violations, god classes, circular imports, provider pattern consistency, connection lifecycle issues, and undocumented layers. Read actual source files -- do not guess. Report severity (CRITICAL/HIGH/MEDIUM/LOW) with file:line references. Include positive findings.

---

**Agent 3: Security** [OPUS] [subagent_type="architect"]

The agent and admin portal are externally exposed via Traefik. Audit: authentication on all endpoints, CSP/CORS/CSRF headers, input sanitization (XSS, SSRF, injection), credential/token storage encryption, rate limiting, subprocess safety, prompt injection defenses, and secrets in logs/telemetry. Read actual source files. Report file:line references. List positive findings too.

---

**Agent 4: Performance** [OPUS] [subagent_type="architect"]

Check startup time, per-request overhead, connection reuse (HTTP clients, DB, Qdrant, MCP). Find resource leaks (clients created but never closed), missing indices, unbounded caches, sync I/O blocking the async event loop. Read actual source files. Report file:line references with severity.

---

$ARGUMENTS

## Model assignment

- Agent 1 (Gemini Analyst): subagent_type="gemini-analyst" -- no model override needed
- Agents 2, 3, 4: subagent_type="architect" -- Opus default (no override)

## After all 4 agents complete

Read `.claude/gemini-audit-output.md` plus the 3 Opus agent outputs, then consolidate into `.claude/plans/YYYY-MM-DD-comprehensive-audit.md` with:

1. Executive summary table (domain x severity counts) -- one row per domain A-M + Architecture + Security + Performance
2. Top 20 priority fixes ranked by risk and effort
3. Full findings per domain (A-M from Gemini, then Architecture, Security, Performance from Opus)
4. Cross-cutting themes found across multiple domains
5. Phased roadmap (critical first, polish last)
