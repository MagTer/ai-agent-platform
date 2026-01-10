# Tri-Model Workflow Guidelines

**Purpose:** Optimize token usage and costs by separating planning (Opus), implementation (Sonnet), and maintenance (Haiku).

**Last Updated:** 2026-01-10

---

## The Problem

- Single long sessions accumulate context tokens
- Context > 200K tokens = higher API costs
- Complex features require extensive exploration
- Using expensive models for simple tasks wastes money

## The Solution

**Three-phase workflow with model specialization:**

1. **Architect (Opus)** - Planning and high-level reasoning
2. **Builder (Sonnet)** - Implementation and debugging
3. **Janitor (Haiku)** - Maintenance and cleanup

Each phase can run in a separate session for maximum token savings.

---

## Phase 1: Planning (Opus - Architect)

**Use for:** Complex features (3+ files), architectural changes, security audits

**Model:** `claude-opus-4-5-20251101`

**Trigger:** `/architect`

### What Architect Does:

1. **Reads project primer** (Turn 1)
   - Gets architecture, standards, patterns from PRIMER.md
   - Establishes foundation

2. **Explores codebase** (Turns 2-4)
   - Finds similar implementations
   - Studies task-specific patterns
   - Understands constraints
   - Identifies integration points

3. **Makes decisions** (Turns 5-6)
   - Chooses architectural approach
   - Defines integration points
   - Selects patterns to follow
   - Identifies dependencies

4. **Writes comprehensive plan** (Turns 7-8)
   - Creates `.claude/plans/YYYY-MM-DD-feature-name.md`
   - Fills all sections with task-specific context
   - Includes code examples
   - Documents decisions and rationale

5. **Asks: Auto-spawn or Manual?** (Turn 9)
   - **[1] Auto-spawn:** Architect spawns Builder automatically (convenient)
   - **[2] Manual:** You start new Builder session (max token savings)

### Output:

```
Plan created: .claude/plans/2026-01-10-redis-caching.md

How would you like to proceed?

[1] Auto-spawn Builder agent now (automatic, same session)
[2] Manual implementation (you start new Builder session for max token savings)

Choose 1 or 2:
```

### Token Usage: ~20-50K tokens

---

## Phase 2: Execution (Sonnet - Builder)

**Use for:** Implementing plans, writing code, debugging

**Model:** `claude-sonnet-4-5-20250929`

**Trigger:** `/builder`

### Starting Builder Session:

**Option 1 - Auto-Spawn (Convenient):**
- Architect spawns Builder automatically
- Stay in same session, Builder has separate context bubble
- Good balance of convenience and cost

**Option 2 - Manual (Max Savings):**
```bash
exit
claude --model sonnet
> /builder .claude/plans/2026-01-10-redis-caching.md
```

### What Builder Does:

1. **Loads context** (Turn 1)
   - Reads PRIMER.md (project essentials)
   - Reads plan file (task-specific context)
   - Confirms success criteria

2. **Implements sequentially** (Turns 2-N)
   - Follows phases in order
   - Creates/modifies files as specified
   - Uses patterns from plan
   - Runs quality checks after each phase
   - Updates todo list in real-time

3. **Validates quality** (Turn N+1)
   - Runs `python scripts/code_check.py`
   - Fixes any issues
   - Re-runs until passes

4. **Reports completion** (Turn N+2)
   - Summarizes what was done
   - Lists files created/modified
   - Confirms success criteria met

### Output:

```
✅ Implementation Complete: Redis Caching

Phases Completed:
- Core Infrastructure
- Module Implementation
- Integration
- Tests
- Quality checks passed

Files Created:
- services/agent/src/core/protocols/cache.py
- services/agent/src/modules/cache/redis_provider.py
- services/agent/tests/unit/test_redis_cache.py

Files Modified:
- services/agent/src/core/providers.py
- services/agent/src/modules/rag/manager.py

Success Criteria: ✅ All met

Ready for review and PR creation.
```

### Token Usage: ~40-80K tokens (fresh session)

---

## Phase 3: Verification & Docs (Haiku - Janitor)

**Use for:** Running tests, updating docs, formatting, summarizing

**Model:** `claude-haiku-4-20250514`

**Trigger:** `/janitor`

### Starting Janitor Session:

```bash
# Can reuse Builder session or start fresh
> /janitor
> Run final tests and update documentation
```

### What Janitor Does:

1. **Runs tests** (Turn 1)
   - Executes pytest with verbose output
   - Reports pass/fail counts
   - Identifies failures with line numbers

2. **Updates documentation** (Turn 2)
   - Identifies docs affected by code changes
   - Updates API documentation
   - Fixes cross-references
   - Verifies consistency

3. **Formats code** (Turn 3)
   - Runs Ruff auto-fixes
   - Runs Black formatting
   - Reports what was cleaned up

4. **Summarizes changes** (Turn 4)
   - Generates PR description
   - Lists files changed
   - Confirms quality checks passed

### Output:

```
✅ Final Checks Complete

Tests: 47/47 passing
Docs: Updated (2 files)
Formatting: All clean

PR Description Generated:
[Markdown summary ready to paste]

Ready to create pull request.
```

### Token Usage: ~5-15K tokens (cheap!)

---

## Cost Comparison

### Without Workflow (Single Session)

```
Session: Opus
- Exploration: 20K tokens
- Planning: 10K tokens
- Implementation: 80K tokens
- Testing: 30K tokens
- Documentation: 10K tokens
= 150K tokens total

Cost: $$$ (higher rate, approaching 200K threshold)
```

### With Workflow (Separated Sessions)

```
Session 1: Opus (Architect)
- Exploration: 20K tokens
- Planning: 10K tokens
= 30K tokens

Session 2: Sonnet (Builder) - NEW context
- Implementation: 40K tokens
- Testing: 20K tokens
= 60K tokens

Session 3: Haiku (Janitor) - NEW context
- Tests: 5K tokens
- Docs: 5K tokens
= 10K tokens

Total: 100K tokens
Cost: $$ (lower rate, cheaper models)

Savings: ~40% cost reduction
```

---

## When to Use Which Workflow

### ✅ Use Tri-Model Workflow for:

- **Complex features** (3+ files, multiple layers)
- **Architectural changes** (new modules, refactoring)
- **Security-sensitive work** (authentication, authorization)
- **Multi-phase implementations** (database + API + tests)
- **Uncertain approaches** (need to explore options)
- **Large scope** (would accumulate 50K+ tokens)

### ❌ Don't use for:

- **Simple bug fixes** (1-2 line changes)
- **Trivial updates** (typos, comments)
- **Documentation only** (no code changes)
- **Quick experiments** (exploratory work)
- **Clear, small tasks** (already know how to implement)

**Rule of thumb:** If it takes less than 3 files and you know exactly what to do, skip the workflow and just do it.

---

## Quick Start Guide

### Scenario 1: Complex Feature

```
User: "Add Redis caching to the RAG module"

Step 1: Plan with Architect
> /architect
> Plan feature: Add Redis caching to RAG module

Step 2: Implement with Builder (new session)
> exit
> claude --model sonnet
> /builder .claude/plans/2026-01-10-redis-caching.md

Step 3: Cleanup with Janitor
> /janitor
> Run final tests and update docs
```

### Scenario 2: Bug Fix

```
User: "Fix the typo in the error message"

Direct Fix (no workflow):
> [Read file, fix typo, run quality checks, done]
```

### Scenario 3: Documentation Update

```
User: "Update the API docs with the new endpoint"

Use Janitor Only:
> /janitor
> Update docs for new /v1/analyze endpoint
```

---

## Best Practices

### For Planning (Architect):

1. **Explore thoroughly** - Don't rush
2. **Copy real examples** - From existing codebase
3. **Be specific** - Exact paths, concrete steps
4. **Explain decisions** - Why this approach?
5. **Include context** - Builder starts fresh

### For Implementation (Builder):

1. **Read plan completely** - Before coding
2. **Follow exactly** - No improvisation
3. **Quality check frequently** - After each phase
4. **Update progress** - Keep todo list current
5. **Ask if unclear** - Don't guess

### For Cleanup (Janitor):

1. **Work quickly** - Haiku is fast
2. **Fix simple issues** - Auto-fixable only
3. **Update docs** - Keep synchronized
4. **Report clearly** - Summarize results
5. **Escalate complexity** - To Builder if needed

### For You (User):

1. **Review plans** - Before implementation
2. **Start fresh sessions** - For cost optimization
3. **Validate results** - After implementation
4. **Choose right tool** - Model appropriate for task
5. **Iterate** - Improve plans over time

---

## Model Selection Quick Reference

| Task | Model | Skill | Why |
|------|-------|-------|-----|
| Complex planning | Opus | `/architect` | High reasoning needed |
| Architecture review | Opus | `/architect` | Complex decisions |
| Security audit | Opus | `/architect` | Critical analysis |
| Writing code | Sonnet | `/builder` | Best coding balance |
| Debugging | Sonnet | `/builder` | Good reasoning + coding |
| Optimization | Sonnet | `/builder` | Performance analysis |
| Running tests | Haiku | `/janitor` | Fast and cheap |
| Updating docs | Haiku | `/janitor` | Simple task |
| Fixing linting | Haiku | `/janitor` | Auto-fixable |
| Summarizing | Haiku | `/janitor` | Quick summary |

---

## Success Metrics

A successful workflow results in:

- ✅ Implementation matches plan
- ✅ Quality checks pass first time
- ✅ Architecture compliance maintained
- ✅ Documentation updated correctly
- ✅ Builder asks < 2 clarifying questions
- ✅ Token usage optimized (right model for each task)
- ✅ Total cost 30-40% lower than single-session

---

## Troubleshooting

### "Builder asks too many clarifying questions"

**Problem:** Plan lacks necessary context

**Solution:**
- Architect needs to explore more thoroughly
- Plan should include more code examples
- Show existing patterns more clearly

### "Quality checks fail repeatedly"

**Problem:** Plan doesn't match project standards

**Solution:**
- Architect should study existing code patterns more
- Include quality requirements in plan
- Reference code_check.py in plan

### "Plan is too high-level"

**Problem:** Not enough concrete steps

**Solution:**
- Use PLAN_TEMPLATE.md more thoroughly
- Include exact file paths and code snippets
- Break down into smaller phases

---

## Remember

The goal is not just to separate planning from implementation, but to:

1. **Use the right tool for each job** - Expensive models for complex reasoning, cheap models for simple tasks
2. **Optimize token usage** - Fresh sessions for each phase
3. **Create comprehensive plans** - Builder can execute autonomously
4. **Maintain quality** - Each phase has validation
5. **Save costs** - 30-40% reduction vs single-session approach

**Start with `/architect` for anything non-trivial. Use `/janitor` for anything simple. Use `/builder` for everything in between.**

---

For detailed skill documentation, see:
- `.claude/skills/architect.md`
- `.claude/skills/builder.md`
- `.claude/skills/janitor.md`
