# Claude Code - AI Agent Platform

**Purpose:** Entry point for Claude Code sessions. Defines the tri-agent workflow.

**Last Updated:** 2026-01-10

---

## Native Sub-Agents (Markdown-Based)

This project uses **native Claude Code sub-agents** defined in `.claude/agents/*.md`. Each agent has specialized instructions and model assignments embedded directly in their YAML frontmatter and markdown content.

---

## The Tri-Agent Workflow

### 1. Architect (Opus - High Reasoning)

**Model:** `claude-4-5-opus`

**Use for:**
- Complex feature planning (3+ files)
- Architecture reviews
- Security audits

**Slash Command:** `/plan`

**What it does:**
- Creates comprehensive implementation plans
- Validates architecture compliance
- Audits security (OWASP Top 10)
- Outputs: `.claude/plans/YYYY-MM-DD-feature.md`

**Example:**
```
/plan Add Redis caching to RAG module with 5min TTL
```

---

### 2. Engineer (Sonnet - Implementation)

**Model:** `claude-4-5-sonnet`

**Use for:**
- Implementing features from plans
- Writing code
- Debugging errors
- API design

**Slash Command:** `/build`

**What it does:**
- Executes implementation plans step-by-step
- Writes production-quality code
- Delegates quality checks to QA agent
- Follows strict coding standards

**Example:**
```
/build .claude/plans/2026-01-10-redis-caching.md
```

---

### 3. QA (Haiku - Quality Assurance)

**Model:** `claude-3-5-haiku` (cost-efficient)

**Use for:**
- Running tests
- Fixing linting errors
- Updating documentation
- Summarizing changes for PRs

**Slash Command:** `/clean`

**What it does:**
- Runs pytest and reports results
- Auto-fixes Ruff/Black issues
- Spawns Engineer for complex Mypy errors
- Updates docs after code changes
- Generates PR descriptions

**Example:**
```
/clean Run tests and update docs
```

---

## Workflow Examples

### Complex Feature (Full Workflow)

```bash
# Step 1: Plan with Architect
/plan Add Azure DevOps integration with OAuth2

# Architect creates: .claude/plans/2026-01-10-azure-devops.md
# Architect offers to auto-spawn Engineer (Option 1) or manual (Option 2)

# Option 1 (Recommended - Auto-spawn):
# Architect spawns Engineer → Engineer implements → Engineer spawns QA → Done!

# Option 2 (Manual - Fresh session):
exit
claude --model sonnet
/build .claude/plans/2026-01-10-azure-devops.md

# Engineer implements feature
# Engineer auto-delegates to QA for final quality checks
# QA runs tests, updates docs, spawns Engineer if complex Mypy errors
# QA reports results back to Engineer
# Engineer confirms completion
```

### Simple Bug Fix (Direct)

```bash
# No workflow needed - just fix it directly
# Read file, fix bug, run quality checks, done
```

### Documentation Update (QA Only)

```bash
/clean Update API docs for new /v1/analyze endpoint
```

---

## Agent Configuration Files

All agent instructions are in `.claude/agents/*.md`:

- **`architect.md`** - Planning logic, architecture rules, security checklist, Engineer spawning
- **`engineer.md`** - Coding standards, implementation patterns, QA delegation
- **`qa.md`** - Quality assurance, testing, docs, Engineer escalation for complex Mypy

**The markdown files contain ALL priming instructions.** No need to read separate PRIMER.md files.

---

## Quality Gate (MANDATORY)

Before completing ANY code changes:

```bash
python scripts/code_check.py
```

This runs: Ruff → Black → Mypy → Pytest

**If this fails, you MUST fix errors. No exceptions.**

---

## Key Constraints (Quick Reference)

**Architecture:**
- 4-layer modular monolith: `interfaces/ → orchestrator/ → modules/ → core/`
- Modules CANNOT import other modules (use Protocol-based DI)
- Core NEVER imports upward

**Code Standards:**
- Lowercase generic types: `list[str]`, `dict[str, int]` (NOT `List`, `Dict`)
- Never use `Any` - always specify concrete types
- Async-first: all I/O must be async
- Absolute imports only (no relative imports)

**Documentation:**
- Swedish for user-facing text; English for code/config
- ASCII-safe punctuation (no emojis or smart quotes)
- Copy/pasteable examples

---

## Cost Optimization

**Model Selection by Task:**

| Task | Agent | Model | Cost |
|------|-------|-------|------|
| Complex planning | Architect | Opus | $$$ |
| Architecture review | Architect | Opus | $$$ |
| Security audit | Architect | Opus | $$$ |
| Writing code | Engineer | Sonnet | $$ |
| Debugging | Engineer | Sonnet | $$ |
| Complex Mypy fixes | Engineer | Sonnet | $$ |
| Running tests | QA | Haiku | $ |
| Fixing simple linting | QA | Haiku | $ |
| Updating docs | QA | Haiku | $ |

**Token Savings:**
- Agents spawn with fresh context (no bloat from parent agent)
- Auto-delegation: Engineer → QA (uses Haiku for tests/docs)
- QA auto-spawns Engineer only when complex errors detected
- Use Haiku for all maintenance tasks (10x cheaper than Sonnet)
- Markdown-embedded instructions avoid loading separate files

---

## When to Use Which Agent

### ✅ Use /plan (Architect - Opus) when:
- Starting a complex feature (3+ files)
- Making architectural changes
- Need security review
- Unclear how to approach a problem
- **Architect will offer to auto-spawn Engineer when done**

### ✅ Use /build (Engineer - Sonnet) when:
- Implementing from an existing plan
- Writing new code
- Debugging errors
- Refactoring with clear scope
- **Engineer will auto-delegate to QA when implementation complete**

### ✅ Use /clean (QA - Haiku) when:
- Running tests
- Fixing linting errors
- Updating documentation
- Summarizing changes
- **QA will auto-spawn Engineer for complex Mypy errors**

### ❌ Don't use workflow for:
- Simple bug fixes (1-2 lines)
- Trivial changes
- Quick experiments

---

## Tech Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI (async)
- **Database:** PostgreSQL (SQLAlchemy 2.0)
- **Vector Store:** Qdrant
- **LLM:** LiteLLM
- **Testing:** Pytest (async)
- **Tools:** Mypy, Ruff, Black, Poetry

---

## Important Files

- `.clinerules` - Project-wide standards (auto-loaded)
- `.claude/agents/*.md` - Agent configurations with embedded instructions
- `.claude/plans/*.md` - Implementation plans created by Architect
- `docs/ARCHITECTURE.md` - Full architecture documentation
- `docs/STYLE.md` - Documentation style guide

---

## Quick Start

1. **For complex features:** Start with `/plan`
2. **For implementation:** Use `/build` with plan file
3. **For cleanup:** Use `/clean` for maintenance
4. **For simple tasks:** Just do them directly

---

**Remember:** The workflow is optimized for autonomy and cost-efficiency. Use the right agent for each task.
