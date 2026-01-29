# Implementation Plans

This directory contains detailed implementation plans created by Opus for execution by Sonnet.

## Purpose

**Cost Optimization Strategy:**
- Planning phase: Opus explores codebase, creates comprehensive plan (context grows)
- Implementation phase: Sonnet starts fresh session, reads plan (context starts at 0)
- Result: Avoid hitting 200K token threshold where pricing increases

## Workflow

### 1. Planning Phase (Opus)

```bash
# Start Opus session with plan mode
claude --model opus
> /plan  # Enter plan mode
```

Opus will:
1. Explore the codebase thoroughly
2. Make architectural decisions
3. Create a detailed plan file: `.claude/plans/YYYY-MM-DD-feature-name.md`
4. Include all context Sonnet needs to implement

### 2. Implementation Phase (Sonnet)

```bash
# Start NEW Sonnet session (fresh context)
claude --model sonnet
> Implement plan from .claude/plans/YYYY-MM-DD-feature-name.md
```

Sonnet will:
1. Read the plan file (gets all context at once)
2. Execute implementation step-by-step
3. Run quality checks
4. Update plan with completion status

## Plan File Naming Convention

```
.claude/plans/YYYY-MM-DD-feature-slug.md
```

Examples:
- `2026-01-09-add-redis-caching.md`
- `2026-01-09-refactor-rag-module.md`
- `2026-01-10-implement-oauth2.md`

## Plan File Structure

See `PLAN_TEMPLATE.md` for the standard structure.
