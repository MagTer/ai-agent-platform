# Claude Code - AI Agent Platform Context

**Purpose:** Root entry point for all Claude Code sessions working on this codebase.

**Last Updated:** 2026-01-10

---

## Critical: Context Hygiene Rules

Before starting any work, follow these rules to minimize token usage and maximize efficiency:

### 1. Plan-First Approach (MANDATORY)

**Before writing any code:**
- Check if a plan exists: `.claude/plans/CURRENT_PLAN.md` or `.claude/plans/YYYY-MM-DD-feature.md`
- For complex tasks (3+ files, architectural changes), a plan is REQUIRED
- If no plan exists, use `/architect` to create one FIRST

**Never:**
- Start coding complex features without a plan
- Explore the entire codebase blindly
- Read files you don't need

### 2. Read Essentials First

**Always read in this order:**
1. **This file** - You're here now
2. **`.claude/PRIMER.md`** - Project architecture, standards, patterns (MANDATORY)
3. **Plan file** - If implementing from a plan
4. **Relevant files only** - Based on plan or specific task

**Avoid:**
- Reading the entire codebase
- Exploring "just to understand"
- Loading unnecessary context

### 3. Stay Focused

**Do:**
- Work on ONE task at a time
- Follow the plan exactly
- Read only what you need
- Ask clarifying questions early

**Don't:**
- Add unrelated features
- Refactor beyond scope
- Explore tangential code
- Accumulate unnecessary context

---

## Tri-Model Workflow

This project uses a strict **Architect → Builder → Janitor** workflow for cost optimization and high autonomy.

### The Architect (Opus - High Reasoning)

**Use for:** Planning, architecture review, security audits

**Trigger:** `/architect` or user asks for "Plan" or "Architecture Review"

**Responsibilities:**
- High-level planning for complex features
- Breaking down user stories into phases
- Architecture compliance validation
- Security auditing (OWASP Top 10)
- Creating comprehensive plans

**Output:** `.claude/plans/YYYY-MM-DD-feature.md`

**Model:** `claude-opus-4-5-20251101`

---

### The Builder (Sonnet - Implementation)

**Use for:** Writing code, debugging, implementing plans

**Trigger:** `/builder` or when implementing from a plan

**Responsibilities:**
- Executing implementation plans step-by-step
- Writing code following PRIMER.md patterns
- Running quality checks (`code_check.py`)
- Debugging specific errors
- API design and optimization

**Constraint:** Works on ONE small step at a time from the plan

**Model:** `claude-sonnet-4-5-20250929`

---

### The Janitor (Haiku - Fast & Cheap)

**Use for:** Maintenance, testing, docs, simple fixes

**Trigger:** `/janitor` or for cleanup tasks

**Responsibilities:**
- Running tests and reporting results
- Fixing simple linting errors
- Updating documentation (README, docs/)
- Formatting code (Black)
- Summarizing changes

**Usage:** Default for all "cleanup" tasks to save costs

**Model:** `claude-haiku-4-20250514` (or latest Haiku)

---

## When to Use Which Model

### Use Opus (/architect) when:
- ✅ Starting a complex feature (3+ files)
- ✅ Making architectural changes
- ✅ Need security review
- ✅ Unclear how to approach a problem
- ✅ User asks for a "plan"

### Use Sonnet (/builder) when:
- ✅ Implementing from an existing plan
- ✅ Writing new code
- ✅ Debugging errors
- ✅ Refactoring with clear scope

### Use Haiku (/janitor) when:
- ✅ Running tests
- ✅ Fixing linting errors
- ✅ Updating documentation
- ✅ Formatting code
- ✅ Simple maintenance tasks

---

## Quick Start Examples

### Example 1: Complex Feature Request

```
User: "Add Redis caching to the RAG module"

Step 1: Use Architect
> /architect
> Plan feature: Add Redis caching to RAG module with 5min TTL

Architect creates: .claude/plans/2026-01-10-redis-caching.md

Step 2: Use Builder (new session for max savings)
> exit
> claude --model sonnet
> /builder
> Implement plan from .claude/plans/2026-01-10-redis-caching.md

Builder implements step-by-step

Step 3: Use Janitor (after implementation)
> /janitor
> Run final tests and update docs

Janitor runs tests, updates docs, reports results
```

### Example 2: Simple Bug Fix

```
User: "Fix the typo in the error message"

Direct Implementation (no planning needed):
- Read the file
- Fix the typo
- Run quality checks
- Done

(Use current model, no workflow needed for trivial tasks)
```

### Example 3: Documentation Update

```
User: "Update the API docs with the new endpoint"

Use Janitor:
> /janitor
> Update docs for new /v1/analyze endpoint

Janitor updates docs, verifies cross-references
```

---

## Cost Optimization Strategy

### The Problem
- Long sessions accumulate context tokens
- Context > 200K tokens = higher API costs
- Complex features require extensive exploration

### The Solution
1. **Architect Session (Opus):** Explores, creates plan (20-50K tokens)
2. **Builder Session (Sonnet - NEW SESSION):** Reads plan, implements (starts at 0 tokens)
3. **Janitor Session (Haiku):** Cleanup, tests, docs (cheap & fast)

**Result:** Stay under 200K threshold, use cheaper models, lower costs

### Best Practice
- **Exit between phases** for maximum token savings
- Start fresh sessions: `exit` then `claude --model sonnet`
- Auto-spawn option available for convenience (same session, separate context bubble)

---

## Model Switching Instructions

**If you need to switch models:**

1. **Identify the right model** for the task (see "When to Use Which Model" above)
2. **Ask the user explicitly:**
   ```
   This task requires [Opus/Sonnet/Haiku] for [reason].

   Please switch to [model]:

   exit
   claude --model [opus/sonnet/haiku]
   > /[architect/builder/janitor]
   ```
3. **Wait for user to switch** - Don't assume you can switch automatically

**Note:** Your environment may support model switching via commands. If so, document it here.

---

## Essential Files Reference

| File | Purpose | When to Read |
|------|---------|--------------|
| **This file** | Workflow entry point | Always first |
| `.claude/PRIMER.md` | Project essentials | Always second |
| `.claude/workflow_guidelines.md` | Detailed workflow | When planning |
| `.clinerules` | Project standards | Loaded automatically |
| `.claude/plans/*.md` | Implementation plans | When implementing |
| `docs/ARCHITECTURE.md` | Full architecture | When making structural changes |

---

## Quality Gate (NON-NEGOTIABLE)

Before completing ANY code changes:

```bash
python scripts/code_check.py
```

**This runs:**
1. Ruff (linting + auto-fixes)
2. Black (formatting)
3. Mypy (strict type checking)
4. Pytest (all tests)

**If this fails, you MUST fix errors. No exceptions.**

---

## Remember

- **Read PRIMER.md** - Contains architecture, standards, patterns
- **Plan first** - For anything non-trivial
- **One task at a time** - Stay focused
- **Right tool for the job** - Use appropriate model
- **Quality gate** - Always run code_check.py

---

## Next Steps

1. Read `.claude/PRIMER.md` (project essentials)
2. Identify which model you should be using
3. Follow the appropriate workflow
4. Execute with focus and discipline

**For detailed workflow, see:** `.claude/workflow_guidelines.md`
