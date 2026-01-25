# Claude Code - AI Agent Platform

**Purpose:** Entry point for Claude Code sessions. Defines the tri-agent workflow.

**Last Updated:** 2026-01-25

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

## Skills & Tools Architecture

The agent uses a **skill-based orchestration pattern**. Understanding this is critical when adding new integrations.

### Skills-Native Execution Architecture

```
User Request
    ↓
[Planner] → Generates plan with skill steps
    ↓
[SkillExecutor] → Runs skill with scoped tools
    ↓
[Tool] → Python class that does the actual work
```

### Key Components

| Component | Purpose |
|-----------|---------|
| `SkillRegistry` | Validates all skills at startup, checks tool references |
| `SkillExecutor` | Runs skills with scoped tool access (ONLY tools in skill.tools) |
| `StepOutcome` | 4-level result: SUCCESS, RETRY, REPLAN, ABORT |
| `StepSupervisorAgent` | Evaluates step output, returns StepOutcome with feedback |

### Self-Correction Flow

```
Execute Step
    ↓
StepSupervisor evaluates
    ↓
┌─────────────────────────────────────────────────┐
│ SUCCESS → Next step                              │
│ RETRY   → Re-execute with feedback (max 1)       │
│ REPLAN  → Generate new plan (max 3 replans)      │
│ ABORT   → Stop execution, return error           │
└─────────────────────────────────────────────────┘
```

**StepOutcome Decision Matrix:**

| Condition | Outcome | Action |
|-----------|---------|--------|
| Step completed successfully | SUCCESS | Proceed to next step |
| Transient error (timeout, rate limit) | RETRY | Re-execute with feedback |
| Output doesn't match intent | REPLAN | Generate new plan |
| Critical error (auth failure, invalid input) | ABORT | Stop and report |

### Why Skills?

Skills are the **primary execution unit**. The Planner generates plans with `executor="skill"` steps:

1. **Isolation** - Each skill has scoped tool access (only tools in frontmatter)
2. **Clarity** - Clear instructions per domain
3. **Security** - Tools aren't directly exposed to the LLM
4. **Self-Correction** - Steps can retry with feedback before replanning

**Note:** `consult_expert` is deprecated. New plans use skills directly with `executor="skill"`.

### Adding a New Integration

**Step 1: Create the Tool** (`core/tools/`)

```python
# services/agent/src/core/tools/my_integration.py
class MyIntegrationTool(Tool):
    name = "my_integration"
    description = "..."
    category = "domain"  # NOT "orchestration"

    async def run(self, action: str, context_id: UUID | None = None, **kwargs) -> str:
        # Implementation
```

**Step 2: Register the Tool** (`config/tools.yaml`)

```yaml
- name: my_integration
  type: core.tools.my_integration.MyIntegrationTool
  enabled: true
  description: "Short description"
```

**Step 3: Create the Skill** (`skills/general/` or `skills/work/`)

```markdown
---
name: "my_skill"
description: "User-facing description for the Planner"
tools: ["my_integration"]
model: agentchat
max_turns: 5
---

# My Skill

**User query:** $ARGUMENTS

## Instructions for the skill...
```

**Step 4: Inject Context (if needed)**

If the tool needs `context_id` for OAuth/credentials, update `executor.py`:

```python
# In _run_tool_gen(), add your tool to the injection block:
if step.tool == "my_integration":
    context_id_str = (request.metadata or {}).get("context_id")
    if context_id_str:
        final_args["context_id"] = UUID(context_id_str)
```

### Directory Structure

```
/skills/                    # Skill markdown files (mounted to /app/skills)
├── general/               # General-purpose skills
│   ├── researcher.md      # Web research with search/fetch
│   ├── deep_researcher.md # Comprehensive multi-source research
│   ├── price_tracker.md   # Price tracking (Prisjakt)
│   └── homey.md          # Smart home control
├── work/                  # Work-related skills
│   └── backlog_manager.md # Azure DevOps backlog management
└── development/           # Development skills
    └── software_engineer.md  # Code investigation/fixes via Claude Code

/services/agent/
├── config/tools.yaml      # Tool registration
└── src/
    ├── core/
    │   ├── tools/         # Tool implementations
    │   │   ├── base.py
    │   │   ├── homey.py
    │   │   ├── git_clone.py      # Clone repos to workspace
    │   │   ├── claude_code.py    # Claude Code subprocess wrapper
    │   │   └── github_pr.py      # Create GitHub PRs
    │   ├── skills/        # Skill infrastructure
    │   │   ├── registry.py       # SkillRegistry (startup validation)
    │   │   └── executor.py       # SkillExecutor (scoped execution)
    │   └── db/
    │       └── models.py  # Database models (Context, Workspace, etc.)
    └── interfaces/
        └── http/
            └── admin_*.py # Admin portal modules
```

### Context-Isolated Workspaces

Each user context has isolated storage for cloned repositories:

```
/tmp/agent-workspaces/
└── {context_id}/          # UUID-based isolation
    ├── repo-name-1/       # Cloned repository
    └── repo-name-2/       # Another repository
```

**Workspace Tracking:** Workspaces are tracked in the `workspaces` database table:

| Field | Purpose |
|-------|---------|
| `context_id` | Links to user's context (multi-tenant) |
| `repo_url` | Git repository URL |
| `local_path` | Path on disk |
| `status` | pending, cloned, syncing, error |
| `last_synced_at` | Last successful sync |

**Admin Portal:** Users manage workspaces via `/platformadmin/workspaces/`

### Skill Frontmatter Reference

```yaml
---
name: "skill_name"           # Unique identifier for the skill
description: "..."           # Shown to Planner for routing decisions
tools: ["tool1", "tool2"]    # ONLY these tools are available (scoped access)
model: agentchat             # LLM model to use (agentchat, skillsrunner)
max_turns: 5                 # Max tool-calling iterations
---
```

**Validation:** SkillRegistry validates all skills at startup. Invalid tool references will log warnings.

### Common Patterns

**OAuth-based integrations** (like Homey):
1. Tool uses `get_token_manager_optional()` to get OAuth tokens
2. Tokens are looked up by `context_id` (user's context)
3. User authorizes via Admin Portal -> OAuth

**Credential-based integrations** (like Azure DevOps):
1. Tool uses `CredentialService` to get per-user credentials
2. Credentials stored encrypted in database
3. User enters credentials via Admin Portal -> Credentials

**Development workflow tools** (git_clone, claude_code, github_pr):
1. `git_clone` clones repo to context-isolated workspace
2. `claude_code` runs Claude Code CLI in "investigate" or "fix" mode
3. `github_pr` creates PRs via `gh` CLI
4. Workflow: Clone -> Investigate -> Fix -> PR

**Tools requiring context injection:**

Some tools need `context_id` or `session` injected at runtime. This is done in `executor.py`:

```python
# In _run_tool_gen():
if step.tool in ("homey", "git_clone"):
    context_id_str = (request.metadata or {}).get("context_id")
    if context_id_str:
        final_args["context_id"] = UUID(context_id_str)

if step.tool in ("git_clone",):
    db_session = (request.metadata or {}).get("_db_session")
    if db_session:
        final_args["session"] = db_session
```

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

**Project Configuration:**
- `.clinerules` - Project-wide standards (auto-loaded)
- `.claude/agents/*.md` - Agent configurations with embedded instructions
- `.claude/plans/*.md` - Implementation plans created by Architect
- `CLAUDE.md` - This file (entry point for Claude Code sessions)

**Documentation:**
- `docs/ARCHITECTURE.md` - Full architecture documentation
- `docs/architecture/02_agent.md` - Agent service architecture details
- `docs/STYLE.md` - Documentation style guide

**Core Architecture:**
- `services/agent/config/tools.yaml` - Tool registration
- `services/agent/src/core/db/models.py` - Database models
- `services/agent/src/core/skills/registry.py` - Skill validation
- `services/agent/src/core/skills/executor.py` - Skill execution
- `services/agent/src/shared/models.py` - Shared Pydantic models (StepOutcome, etc.)

**Admin Portal:**
- `services/agent/src/interfaces/http/admin_shared.py` - Navigation, shared components
- `services/agent/src/interfaces/http/admin_*.py` - Individual admin modules

---

## Admin Portal

The Admin Portal (`/platformadmin/`) provides management interfaces for the platform.

### Architecture

```
interfaces/http/
├── app.py                    # FastAPI app, router registration
├── admin_shared.py           # Navigation, shared components
├── admin_dashboard.py        # Main dashboard
├── admin_oauth.py            # OAuth provider management
├── admin_credentials.py      # User credential management
├── admin_contexts.py         # Context (workspace) management
├── admin_workspaces.py       # Git repository workspaces
└── templates/                # Large HTML templates (40KB+)
```

### Navigation System

Navigation is defined in `admin_shared.py`:

```python
NAV_ITEMS = [
    NavItem("Dashboard", "/platformadmin/", "&#127968;", "main"),
    NavItem("Contexts", "/platformadmin/contexts/", "&#128194;", "features"),
    NavItem("Workspaces", "/platformadmin/workspaces/", "&#128193;", "features"),
    NavItem("OAuth", "/platformadmin/oauth/", "&#128274;", "features"),
    NavItem("Credentials", "/platformadmin/credentials/", "&#128273;", "features"),
]
```

### Adding a New Admin Module

1. **Create the router file** (`interfaces/http/admin_mymodule.py`):

```python
from fastapi import APIRouter, Depends
from interfaces.http.admin_shared import admin_page_layout, NAV_ITEMS

router = APIRouter(prefix="/platformadmin/mymodule", tags=["admin-mymodule"])

@router.get("/")
async def mymodule_dashboard():
    content = "<h2>My Module</h2>..."
    return HTMLResponse(admin_page_layout("My Module", content, NAV_ITEMS))
```

2. **Register the router** in `app.py`:

```python
from interfaces.http.admin_mymodule import router as admin_mymodule_router
app.include_router(admin_mymodule_router)
```

3. **Add navigation item** in `admin_shared.py`:

```python
NavItem("My Module", "/platformadmin/mymodule/", "&#128736;", "features"),
```

### Portal Patterns

- **Dashboard pattern**: Main page with cards/stats, modal forms for CRUD
- **List pattern**: Table with actions (edit, delete, sync)
- **Async operations**: Use background tasks with polling for long operations
- **Error handling**: HTTPException with user-friendly messages

---

## Testing Guidelines

### Test Structure

```
services/agent/
├── src/
│   └── core/
│       └── tests/            # Unit tests near source code
│           ├── test_service.py
│           ├── test_supervisors.py
│           └── test_skill_registry.py
└── tests/                    # Integration tests
    └── test_integration.py
```

### Running Tests

```bash
# All tests (via stack CLI)
./stack test

# Specific test file
pytest services/agent/src/core/tests/test_service.py -v

# Single test
pytest services/agent/src/core/tests/test_service.py::test_function_name -v

# With coverage
pytest --cov=services/agent/src --cov-report=html
```

### Test Patterns

**Async Tests** (required for all async code):

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_async_function():
    mock_client = AsyncMock()
    mock_client.call.return_value = "result"

    result = await my_async_function(mock_client)

    assert result == "expected"
    mock_client.call.assert_called_once_with("arg")
```

**Fixtures for Database**:

```python
@pytest.fixture
async def db_session():
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()
```

**Mocking LLM Responses**:

```python
@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.generate = AsyncMock(return_value='{"outcome": "success"}')
    return llm
```

### TDD Workflow

1. **Write failing test first** - Define expected behavior
2. **Implement minimum code** - Make test pass
3. **Refactor** - Clean up while tests stay green
4. **Run `stack check`** - Ensure all quality gates pass

### What to Test

| Component | What to Test |
|-----------|--------------|
| Tools | Input validation, error handling, API responses |
| Skills | Tool scoping, execution flow |
| Agents | Plan generation, outcome handling |
| Services | Integration of components |
| API endpoints | Request/response contracts |

### Test Naming

```python
# Pattern: test_<action>_<scenario>_<expected_result>
def test_parse_response_with_missing_field_raises_error():
    ...

def test_execute_step_returns_retry_on_timeout():
    ...
```

---

## Quick Start

1. **For complex features:** Start with `/plan`
2. **For implementation:** Use `/build` with plan file
3. **For cleanup:** Use `/clean` for maintenance
4. **For simple repetitive tasks:** Delegate to Haiku via `Task(subagent_type="simple-tasks", model="haiku", ...)`
5. **For trivial one-off fixes:** Do directly (1-2 edits max)

---

**Remember:** The workflow is optimized for autonomy and cost-efficiency. Use the right agent for each task.
