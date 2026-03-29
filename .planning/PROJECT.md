# AI Agent Platform

## What This Is

A personal multi-user AI agent platform built around skills-based agentic workflows. The platform lets Magnus interact with an AI assistant via Telegram and Open WebUI, backed by a 4-layer Python/FastAPI monolith that orchestrates LLM reasoning, tool use, and RAG retrieval across isolated per-user contexts. All coding, security, and verification is done by AI — Magnus directs, Claude builds.

## Core Value

The agent reliably executes multi-step agentic workflows (research, smart home control, backlog management, code fixes) with correct output format and self-correcting behavior — so Magnus can trust the result without checking under the hood.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- ✓ Multi-tenant context-scoped execution — existing
- ✓ Skills-based orchestration (PlannerAgent → SkillExecutor → StepSupervisorAgent) — existing
- ✓ Self-correction via StepOutcome (SUCCESS/RETRY/REPLAN/ABORT) — existing
- ✓ Classic RAG retrieval via Qdrant vector store — existing
- ✓ Tool integrations: Azure DevOps, Homey smart home, Telegram, GitHub — existing
- ✓ Admin portal with context/user/scheduler/workspace management — existing
- ✓ Scheduled job execution (SchedulerAdapter, cron-based) — existing
- ✓ MCP server management (per-context, OAuth/Bearer/None auth) — existing
- ✓ Human-in-the-loop (HITL) draft/approval workflow — existing
- ✓ OAuth + encrypted credential management — existing
- ✓ Observability: OTel tracing, debug logs, Diagnostic API — existing
- ✓ Skill quality self-evaluation (SkillQualityAnalyser) — existing

### Active

<!-- Current scope. Building toward these. -->

- [ ] Agentic RAG: expose retrieval as an active tool/skill in the reasoning loop (replaces blind ragproxy injection)
- [ ] Semantic/hierarchical document chunking to improve retrieval quality (replace naive 1000-char chunking)
- [ ] Retrieval evaluation in StepSupervisorAgent (trigger REPLAN/RETRY when retrieved context is insufficient)
- [ ] Skill output formatting improvements — reduce LLM-generated format deviations
- [ ] Qdrant authentication enabled (currently unauthenticated on Docker network)
- [ ] HITL robustness: replace fragile regex draft extraction with structured output
- [ ] Admin portal UX improvements (contexts module split, improved navigation)
- [ ] Performance/cost: smarter LLM routing, response caching where safe

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Public SaaS / external user onboarding — this is a personal platform, not a product
- Native mobile app — Telegram covers mobile access adequately
- Real-time multi-user collaboration — single-owner use case, not needed
- Replacing the Python/FastAPI stack — architectural investment is too deep
- Removing source volume mount from prod docker-compose without explicit decision — deferred until CI/CD pipeline matures

## Context

This is a brownfield platform in active use. Magnus is not a developer — Claude (via Claude Code and the GSD workflow) is responsible for all implementation, testing, security, and architecture decisions. The expectation is that Claude stays current with AI engineering trends and proactively suggests better approaches (e.g., the Agentic RAG initiative came from a Gemini recommendation).

**Current state at GSD initialization (2026-03-28):**
- Platform is mostly working with rough edges around skill output formatting
- Classic RAG is in place but retrieval quality is limited by naive chunking and blind context injection
- Two execution code paths still live in service.py (SkillExecutor + legacy StepExecutorAgent fallback)
- Dispatcher async refactor is deferred (streaming simulation adds latency)
- Admin modules (admin_contexts.py at 2452 lines) approaching maintainability threshold

**Stack:** Python 3.12, FastAPI, PostgreSQL/SQLAlchemy 2.0, Qdrant, LiteLLM → OpenRouter, Telegram, Docker

## Constraints

- **Stack**: Python 3.12 + FastAPI — no language or framework changes
- **Architecture**: 4-layer modular monolith with strict layer boundaries — modules cannot cross-import
- **Language**: English for all code, configs, docs, commits; Swedish only for end-user chat responses
- **Quality gate**: `stack check` (ruff + black + mypy + pytest) must pass before every PR push
- **Git safety**: All changes via PRs; no direct pushes to main; ops agent handles all git operations
- **Security**: OWASP Top 10 compliance; no credentials in code or logs; all tokens encrypted at rest
- **Dependency discipline**: Check stdlib alternatives before adding new pip packages

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Skills-native execution as primary path | Isolation, scoped tool access, clear instructions per domain | ✓ Good |
| Context-scoped credentials (context_id, not user_id) | Multi-tenant isolation, supports per-context integrations | ✓ Good |
| LiteLLM proxy → OpenRouter (gpt-oss-120b:exacto) | Model flexibility without vendor lock-in | — Pending |
| Debug logs to JSONL (not DB) | Avoids DB write amplification for high-volume debug events | ✓ Good |
| GSD workflow for all platform improvements | AI-driven development with planning/verification gates | — Pending |
| Agentic RAG over Classic RAG | Retrieval as active tool improves relevance; blind injection wastes context | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-28 after GSD initialization*
