# Claude Code - AI Agent Platform

**Purpose:** Entry point for Claude Code sessions. Defines the tri-agent workflow.

**Last Updated:** 2026-01-23

---

## Native Sub-Agents (Markdown-Based)

This project uses **native Claude Code sub-agents** defined in `.claude/agents/*.md`. Each agent has specialized instructions and model assignments embedded directly in their YAML frontmatter and markdown content.

---

## The Agent Workflow

**Four agents** optimized for different task types and costs:

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

### 4. Simple Tasks (Haiku - Cost Saver)

**Model:** `claude-3-5-haiku` (cheapest)

**Use for:**
- Text translations/fixes (e.g., Swedish -> English)
- Find-and-replace across files
- Adding type hints or docstrings
- Simple boilerplate generation
- Formatting cleanup

**No slash command** - delegate via Task tool with `model: "haiku"`:

```python
Task(
    subagent_type="simple-tasks",
    model="haiku",
    description="Fix Swedish UI text",
    prompt="Change all Swedish UI strings to English in admin_dashboard.py"
)
```

**Why this matters:** Simple repetitive tasks don't need Opus/Sonnet reasoning. Haiku is 10-20x cheaper and fast enough for find-replace style work.

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
- **`simple-tasks.md`** - Cost-efficient agent for repetitive edits (text fixes, find-replace)

**The markdown files contain ALL priming instructions.** No need to read separate PRIMER.md files.

---

## Quality Gate (MANDATORY)

Before completing ANY code changes:

```bash
stack check
```

This runs: Ruff → Black → Mypy → Pytest

Use `stack check --no-fix` for CI-style check-only mode.

**If this fails, you MUST fix errors. No exceptions.**

---

## Git Workflow (Branch Protection)

**IMPORTANT:** The `main` branch has branch protection enabled. Direct pushes are blocked.

### Dev vs Production Deployment

**Dev environment** - Fast iteration:
- Deploy directly with `./stack dev restart` (no PR needed)
- Always commit changes first to avoid losing work
- Use for testing and rapid development

**Production** - Requires PR:
- Must go through main branch (PR + CI)
- CI takes time, so batch production deploys
- Deploy to prod periodically, not after every change

**Recommended workflow:**
1. Make changes and commit to a feature branch
2. Deploy to dev: `./stack dev restart`
3. Test in dev environment
4. When ready for prod: create PR, wait for CI, merge, then `./stack deploy`

### Commit Process

**CRITICAL: Always commit before deploying to dev.** This ensures work is never lost.

1. **Run quality checks** (MANDATORY for code changes):
   ```bash
   stack check
   ```
   This runs Ruff, Black, Mypy, and Pytest. **Do not proceed if this fails.**

2. **Check for auto-fixed files** (IMPORTANT):
   ```bash
   git status
   ```
   `stack check` auto-fixes import sorting and formatting. If files were modified,
   they must be included in your commit or CI will fail (CI runs with `--no-fix`).

3. **Preserve unrelated work** (CRITICAL - prevents data loss):
   ```bash
   # If committing only SOME files, stash unrelated changes first:
   git stash push -m "WIP: other work" -- path/to/unrelated/

   # Or save everything to a WIP branch first:
   git stash push -m "WIP: preserve all uncommitted work"
   ```

4. **Create a feature branch** from your changes:
   ```bash
   git checkout -b feat/description   # or fix/description
   ```

5. **Commit ALL changes** (including auto-fixed files):
   ```bash
   git add -A && git commit -m "feat: Description"
   ```

6. **Push the branch** to origin:
   ```bash
   git push -u origin feat/description
   ```

7. **Create a PR** using GitHub CLI:
   ```bash
   gh pr create --title "feat: Description" --body "..."
   ```

8. **Return to main safely** (IMPORTANT - check for uncommitted work first):
   ```bash
   git status                    # Check for uncommitted changes
   git stash list                # Check for stashed work
   git checkout main             # Switch to main (will warn if uncommitted changes)
   git pull origin main          # Get latest (safer than reset --hard)
   ```

   **Only use `git reset --hard` if you're certain no work will be lost:**
   ```bash
   git checkout main && git reset --hard origin/main
   ```

9. **Restore stashed work** (if you stashed in step 3):
   ```bash
   git stash pop                 # Restore and remove from stash
   # Or: git stash apply         # Restore but keep in stash
   ```

### Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feat/description` | `feat/email-service` |
| Bug fix | `fix/description` | `fix/oauth-redirect` |
| Refactor | `refactor/description` | `refactor/tool-registry` |
| Docs | `docs/description` | `docs/architecture-diagram` |

### Never Do

- Push without running `stack check` first - CI will fail
- `git push origin main` - Will be rejected by branch protection
- `git push --force` on shared branches - Destructive
- Skip PR review for non-trivial changes
- **`git reset --hard` without checking `git status` first** - Destroys uncommitted work
- Commit only some files without stashing unrelated changes first - Risk of losing work on reset

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

**Language:**
- Swedish ONLY for conversation with user (chat messages)
- English for ALL code, web content, UI text, config, comments, docs
- ASCII-safe punctuation (no emojis or smart quotes)
- Copy/pasteable examples

**HTML Templates:**
- Separate HTML from Python when file exceeds 500 lines of HTML OR 40KB total
- Store templates in `interfaces/http/templates/*.html`
- Load via `Path(__file__).parent / "templates" / "name.html"`
- Keep API endpoints in `admin_*.py`, HTML/CSS/JS in templates

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
| Text translations | Simple Tasks | Haiku | $ |
| Find-and-replace | Simple Tasks | Haiku | $ |
| Adding type hints | Simple Tasks | Haiku | $ |

**CRITICAL - Delegate Simple Tasks:**

When in an Opus/Sonnet session and you encounter simple, repetitive tasks:
- **DO NOT** do them in the main context
- **DO** spawn a Haiku agent via Task tool

Examples of tasks to delegate:
- Translating UI text (Swedish -> English)
- Renaming a variable across 10 files
- Adding docstrings to functions
- Fixing the same typo in multiple places

```python
# GOOD - delegate to Haiku
Task(subagent_type="simple-tasks", model="haiku", prompt="...")

# BAD - doing repetitive edits in Opus context
Edit(...) Edit(...) Edit(...) Edit(...)  # Expensive!
```

**Token Savings:**
- Agents spawn with fresh context (no bloat from parent agent)
- Auto-delegation: Engineer → QA (uses Haiku for tests/docs)
- QA auto-spawns Engineer only when complex errors detected
- Use Haiku for all maintenance tasks (10x cheaper than Sonnet)
- Markdown-embedded instructions avoid loading separate files
- **Proactively delegate simple repetitive tasks to Haiku**

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

### ✅ Delegate to Simple Tasks (Haiku) when:
- Translating/fixing text across files
- Find-and-replace operations
- Adding boilerplate (type hints, docstrings)
- Any repetitive edit task (5+ similar edits)
- **Spawn via Task tool with `model: "haiku"`**

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

## Stack CLI

The project uses a custom `stack` CLI for all operations. **Always run from project root with `./stack`**.

### Common Commands

```bash
# Quality checks (MANDATORY before commits)
./stack check              # Run ruff, black, mypy, pytest (auto-fix enabled)
./stack check --no-fix     # CI mode (no auto-fix)

# Development environment
./stack dev up             # Start dev environment
./stack dev down           # Stop dev environment
./stack dev restart        # Restart dev environment
./stack dev logs           # View dev logs
./stack dev status         # Show dev container status

# Production
./stack deploy             # Deploy to production (runs checks first)
./stack deploy --skip-checks  # Skip quality checks (use with caution)
./stack up                 # Start production stack
./stack down               # Stop production stack
./stack restart            # Restart production stack
./stack logs [service]     # Tail production logs
./stack status             # Show production container status
./stack health             # Check service health

# Individual checks
./stack lint               # Run ruff + black only
./stack typecheck          # Run mypy only
./stack test               # Run pytest only
```

### Deployment Workflow

```bash
# Dev deployment (restart with latest code)
./stack dev restart

# Production deployment (full workflow)
./stack deploy
# This automatically:
# 1. Verifies you're on main branch
# 2. Runs all quality checks
# 3. Rebuilds the agent container
# 4. Restarts production with zero downtime
```

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
4. **For simple repetitive tasks:** Delegate to Haiku via `Task(subagent_type="simple-tasks", model="haiku", ...)`
5. **For trivial one-off fixes:** Do directly (1-2 edits max)

---

**Remember:** The workflow is optimized for autonomy and cost-efficiency. Use the right agent for each task.
