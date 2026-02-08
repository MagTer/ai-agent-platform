Launch 11 parallel architect agents (Opus) to perform a comprehensive platform audit. Each agent independently explores the full codebase and reports findings.

Spawn ALL of these agents simultaneously using the Task tool with subagent_type="architect":

1. **Architecture** -- Validate the 4-layer modular monolith (interfaces -> orchestrator -> modules -> core). Check for layer violations, god classes, circular imports, provider pattern consistency, connection lifecycle issues, and undocumented layers. Report severity (critical/high/medium/low) with file:line references.

2. **Documentation** -- Compare all docs (CLAUDE.md, docs/*.md, README, .env.template, skill frontmatter) against actual code. Find stale references, wrong paths, outdated protocol names, missing docs for existing features, and config values that reference removed components.

3. **Security** -- The agent and admin portal are externally exposed via Traefik. Audit: authentication on all endpoints, CSP/CORS/CSRF headers, input sanitization (XSS, SSRF, injection), credential/token storage encryption, rate limiting, subprocess safety, prompt injection defenses, and secrets in logs/telemetry. List positive findings too.

4. **Component Functionality** -- Assess maturity (LOW/MEDIUM/HIGH/EXCELLENT) of each component: orchestrator, planner, skill system, each tool, LiteLLM client, RAG module, MCP client, conversation management, SSE streaming. For each: strengths, gaps, and runtime bugs.

5. **Dead Code** -- Find unregistered tools, unused modules, orphaned scripts, duplicate configs, unused dependencies in pyproject.toml, deprecated settings still in code, tools registered but referenced by zero skills.

6. **Performance** -- Check startup time, per-request overhead, connection reuse (HTTP clients, DB, Qdrant, MCP). Find resource leaks (clients created but never closed), missing indices, unbounded caches, sync I/O blocking async event loop.

7. **Logging & Observability** -- Map the current observability stack (OTel spans, debug logs, security events, diagnostic API, admin dashboard). Find gaps: missing trace spans, unqueryable data, debug-only logging that should be always-on, missing per-conversation diagnosis, latency percentile gaps.

8. **Testing Gaps** -- Estimate coverage. Find untested security-critical code (auth, CSRF, input validation), untested core paths (planner, dispatcher, supervisor), CI testpath misconfigurations, missing test infrastructure (factories, fixtures), and gaps in regression/integration/semantic test categories.

9. **Stack Script** -- Review the stack CLI (Typer+Rich). Check: subprocess safety, data-destructive commands without confirmation, duplicate logic, missing timeouts, CI/stack parity, error handling, and UX consistency.

10. **CI/CD Pipeline** -- Review GitHub Actions workflow. Check: build optimization (BuildKit, caching, layer order), missing security scanning (dependency audit, container scan), test coverage reporting, deployment verification, branch protection gaps.

11. **Agent Config & Architecture Guards** -- Review .claude/agents/*.md, .clinerules, CLAUDE.md, and the architecture validator. Check: validator enforcement (warn-only vs fail), CI integration, stale references, dependency matrix accuracy, missing validation rules (upward imports, orchestrator layer, TYPE_CHECKING blocks).

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
3. Full findings per area (A-K sections)
4. Cross-cutting themes found across multiple analyses
5. Phased roadmap (critical first, polish last)

Use subagent_type="architect" for all 11 agents. Each agent should be launched with run_in_background=true for maximum parallelism.
