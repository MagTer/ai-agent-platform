# Claude Code - AI Agent Platform

**Purpose:** Entry point for Claude Code sessions. Defines the multi-agent workflow.

**Last Updated:** 2026-02-13

---

## ⚠️ CRITICAL SAFETY RULES - READ FIRST

### Git Operations: MANDATORY DELEGATION

**BEFORE running ANY git command, STOP and check:**

```python
# ✅ CORRECT - Delegate to ops agent
Task(
    subagent_type="ops",
    description="commit and create PR",
    prompt="Commit current changes and create a PR"
)

# ❌ FORBIDDEN - Direct git commands
git reset --hard    # DESTROYS uncommitted work
git stash          # HIDES work, gets lost
git push --force   # OVERWRITES remote
git checkout .     # DISCARDS changes
git clean -f       # DELETES files
```

**Why?** The ops agent has safety checks. Direct git commands risk data loss.

**Allowed read-only commands:** `git status`, `git diff`, `git log`, `git show`

**Everything else:** Use ops agent via Task tool.

**Enforcement:** `.git-safety-check.sh` script blocks forbidden commands.

**Audit:** Run `.claude/git-audit.sh` to check for violations.

---

## Native Sub-Agents (Markdown-Based)

This project uses **native Claude Code sub-agents** defined in `.claude/agents/*.md`. Each agent has specialized instructions and model assignments embedded directly in their YAML frontmatter and markdown content.

---

## The Agent Workflow

**Four agents** optimized for different task types and costs:

### 1. Architect (Opus - High Reasoning)

**Model:** Opus

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

**Model:** Sonnet

**Use for:**
- Implementing features from plans
- Writing code
- Debugging errors
- API design

**Slash Command:** `/build`

**What it does:**
- Executes implementation plans step-by-step
- Writes production-quality code
- Delegates quality checks to Ops agent
- Follows strict coding standards

**Example:**
```
/build .claude/plans/2026-01-10-redis-caching.md
```

---

### 3. Ops (Haiku - Git, Test, Deploy)

**Model:** Haiku (cost-efficient)

**Use for:**
- ALL git operations (commit, push, sync, PR)
- Running tests and quality checks
- Deploying to dev/prod
- Branch management

**Slash Command:** `/ops`

**What it does:**
- Handles git safely (NEVER uses destructive commands)
- Runs `stack check` for quality verification
- Creates PRs with proper descriptions
- Deploys via `stack dev deploy` / `stack deploy`

**Example:**
```
/ops commit and create PR for current changes
/ops deploy to dev
/ops run quality checks
```

**CRITICAL:** This agent has strict git safety rules. It will NEVER run `git reset --hard` or other destructive commands.

---

### 4. Simple Tasks (Haiku - Cost Saver)

**Model:** Haiku (cheapest)

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
    prompt="Change all Swedish UI strings to English in admin_portal.py"
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
# Architect spawns Engineer → Engineer implements → Engineer spawns Ops → Done!

# Option 2 (Manual - Fresh session):
exit
claude --model sonnet
/build .claude/plans/2026-01-10-azure-devops.md

# Engineer implements feature
# Engineer delegates to Ops for quality checks and commit
# Ops runs tests, creates PR
# Ops reports results back to Engineer
# Engineer confirms completion
```

### Simple Bug Fix (Direct)

```bash
# No workflow needed - just fix it directly
# Read file, fix bug, run quality checks, done
```

### Run Tests and Deploy

```bash
/ops run quality checks and deploy to dev
```

---

## Agent Configuration Files

All agent instructions are in `.claude/agents/*.md`:

- **`architect.md`** - Planning logic, architecture rules, security checklist, Engineer spawning
- **`engineer.md`** - Coding standards, implementation patterns, Ops delegation
- **`ops.md`** - Git safety, quality checks, deployment, PR workflow
- **`simple-tasks.md`** - Cost-efficient agent for repetitive edits (text fixes, find-replace)

**The markdown files contain ALL priming instructions.** No need to read separate PRIMER.md files.

---

## Quality Gate (MANDATORY)

**`stack check` must pass before EVERY push to a PR branch** — not just when first creating
the PR. This includes merge conflict resolution commits, follow-up fix commits, and any other
push that CI will see.

```bash
stack check           # Run from repo root with auto-fix (default)
stack check --no-fix  # CI-style check-only mode
```

This runs (matching CI exactly): Ruff → Black → Mypy → Pytest

**Never include "Do NOT run stack check" in ops agent prompts for any PR push.**
Stack check takes ~3 minutes; a CI failure costs far more time to diagnose and fix.

### What counts as a source file

All of the following must be staged before pushing — they are **source files, not artifacts**:
- Python (`.py`) — always
- HTML templates (`templates/*.html`)
- YAML configs (`config/tools.yaml`, skill `.md` files)
- Alembic migrations (`alembic/versions/*.py`)

Build artifacts to skip: `.testmondata`, `.venv/`, `__pycache__/`, `.stack/dev-deployments.json`

**If this fails, you MUST fix errors. No exceptions.**

---

## Git, Test & Deploy - USE OPS AGENT

**CRITICAL: ALWAYS delegate git, test, and deploy operations to the Ops agent.**

```python
# For ANY git operation, quality check, or deployment:
Task(
    subagent_type="ops",
    description="commit and create PR",
    prompt="Commit current changes and create a PR"
)
```

**Why?** The Ops agent has strict safety rules that prevent destructive commands like `git reset --hard`. Running git commands directly risks losing uncommitted work.

**Enforcement:** The `.git-safety-check.sh` script blocks forbidden git commands. It checks for command chaining (e.g., `sleep 10 && git reset --hard`) and common bypass patterns.

**Audit:** Run `.claude/git-audit.sh` to check recent commits and bash history for violations.

**IMPORTANT: Before spawning an ops agent for branch/PR operations, warn it about any uncommitted changes visible in `git status` that are NOT part of the current task.** The ops agent must commit or preserve ALL uncommitted work before switching branches -- `git checkout` silently discards uncommitted changes to tracked files.

### What Ops Agent Handles

| Operation | Example |
|-----------|---------|
| Commit changes | `/ops commit with message "feat: add feature"` |
| Create PR | `/ops create PR for current branch` |
| Sync branch | `/ops sync with origin/main` |
| Quality checks | `/ops run stack check` |
| Deploy to dev | `/ops deploy to dev` |
| Deploy to prod | `/ops deploy to production` |

### Branch Protection

- `main` branch has protection enabled - direct pushes blocked
- All changes must go through PRs
- Ops agent handles the PR workflow safely

### Semantic Tests (Before Prod Deploy)

| Category | Tests | When to run |
|----------|-------|-------------|
| routing | 5 | Always before prod deploy |
| regression | 3 | After model/prompt changes |
| skills | 11 | After skill changes |
| tools | 4 | After tool changes |

```bash
./stack test --semantic-category routing  # Fast (~30s)
./stack test --semantic                   # Full regression
```

---

## Key Constraints (Quick Reference)

**Architecture:**
- 4-layer modular monolith: `interfaces/ → orchestrator/ → modules/ → core/`
- Modules CANNOT import other modules (use Protocol-based DI)
- Core NEVER imports upward
- State hierarchy: `Context -> Conversation -> Session -> Message` (all in PostgreSQL)
- Every Agent request MUST resolve to an active Session

**Code Standards:**
- Lowercase generic types: `list[str]`, `dict[str, int]` (NOT `List`, `Dict`)
- Never use `Any` - always specify concrete types
- Async-first: all I/O must be async
- Absolute imports only (no relative imports)
- McCabe complexity < 18

**Safety:**
- Never output API keys or credentials in responses
- Do NOT edit `docker-compose.yml` without explicit user approval
- Do NOT add new pip dependencies without checking if stdlib alternative exists

**Language:**
- English for ALL code, GUI, config, admin interfaces, comments, docs, plans, commit messages
- Swedish only for end-user chat responses (bot messages to users)
- Respond in English regardless of what language the user writes in
- ASCII-safe punctuation (no emojis or smart quotes)

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

**Note:** Plans use skills directly with `executor="skill"` and the `SkillExecutor` handles execution with scoped tool access.

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
    │   ├── agents/        # LLM agent implementations
    │   │   ├── planner.py        # PlannerAgent (generates plans)
    │   │   ├── executor.py       # StepExecutorAgent (runs plan steps)
    │   │   └── supervisors.py    # StepSupervisorAgent (evaluates outcomes)
    │   ├── auth/          # Authentication and credentials
    │   │   └── credential_service.py  # CredentialService (context-scoped)
    │   ├── context/       # Shared context resolution
    │   │   └── service.py        # ContextService (all adapters use this)
    │   ├── runtime/       # Agent runtime (decomposed from AgentService)
    │   │   ├── service.py        # AgentService (main orchestrator)
    │   │   ├── persistence.py    # DB CRUD for conversations/sessions/messages
    │   │   ├── context_injector.py  # File/workspace injection with security
    │   │   ├── tool_runner.py    # Tool invocation and schema generation
    │   │   ├── hitl.py           # Human-in-the-loop workflow coordinator
    │   │   ├── config.py         # RuntimeConfig (all runtime settings)
    │   │   └── litellm_client.py # LiteLLM client wrapper
    │   ├── observability/ # Tracing and monitoring
    │   │   ├── tracing.py        # Span export with size-based rotation
    │   │   └── error_codes.py    # Structured error codes
    │   ├── tools/         # Tool implementations
    │   │   ├── base.py
    │   │   ├── azure_devops.py   # Azure DevOps work items (context-scoped)
    │   │   ├── homey.py
    │   │   ├── git_clone.py      # Clone repos to workspace
    │   │   ├── claude_code.py    # Claude Code subprocess wrapper
    │   │   └── github_pr.py      # Create GitHub PRs
    │   ├── skills/        # Skill infrastructure
    │   │   ├── registry.py       # SkillRegistry (startup validation)
    │   │   └── executor.py       # SkillExecutor (scoped execution)
    │   ├── db/
    │   │   └── models.py  # Database models (Context, Workspace, etc.)
    │   └── tests/         # Unit tests (37+ files, 890+ tests)
    │       ├── mocks.py          # MockLLMClient, test fixtures
    │       ├── test_planner_agent.py
    │       ├── test_executor_agent.py
    │       └── ...
    ├── shared/
    │   └── chunk_filter.py       # ChunkFilter (verbosity + safety filtering)
    └── interfaces/
        ├── base.py               # PlatformAdapter ABC (platform_name)
        ├── http/
        │   ├── admin_*.py        # Admin portal modules
        │   └── templates/        # HTML templates (40KB+ files)
        ├── scheduler/
        │   └── adapter.py        # SchedulerAdapter (cron-based job execution)
        └── telegram/
            └── adapter.py        # Telegram adapter (uses ContextService + ChunkFilter)
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
1. Tool uses `CredentialService` to get per-context credentials
2. Credentials stored encrypted in database, scoped to `context_id`
3. User enters credentials via Admin Portal -> Context Detail -> Credentials tab

**Development workflow tools** (git_clone, claude_code, github_pr):
1. `git_clone` clones repo to context-isolated workspace
2. `claude_code` runs Claude Code CLI in "investigate" or "fix" mode
3. `github_pr` creates PRs via `gh` CLI
4. Workflow: Clone -> Investigate -> Fix -> PR

**Tools requiring context injection:**

Some tools need `context_id` or `session` injected at runtime. This is done in `executor.py`:

```python
# In _run_tool_gen():
if step.tool in ("homey", "git_clone", "azure_devops"):
    context_id_str = (request.metadata or {}).get("context_id")
    if context_id_str:
        final_args["context_id"] = UUID(context_id_str)

if step.tool in ("git_clone", "azure_devops"):
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
| Git operations | Ops | Haiku | $ |
| Running tests | Ops | Haiku | $ |
| Deployment | Ops | Haiku | $ |
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
- Auto-delegation: Engineer → Ops (uses Haiku for git/test/deploy)
- Ops auto-spawns Engineer only when complex errors detected
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
- **Engineer will auto-delegate to Ops when implementation complete**

### ✅ Use /ops (Ops - Haiku) when:
- ANY git operation (commit, push, sync, PR)
- Running tests or quality checks
- Deploying to dev or production
- **Ops will auto-spawn Engineer for complex errors**

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
./stack dev deploy         # Build, deploy, verify health (USE THIS)
./stack dev restart        # Quick restart (no build, no health check)
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
# Dev deployment (build + health verification)
./stack dev deploy

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
- `CLAUDE.md` - This file (entry point for Claude Code sessions)
- `.claude/agents/*.md` - Agent configurations with embedded instructions
- `.claude/commands/*.md` - Slash commands (`/plan`, `/build`, `/ops`) that delegate to agents
- `.claude/plans/*.md` - Implementation plans created by Architect

**Documentation:**
- `docs/ARCHITECTURE.md` - Full architecture documentation
- `docs/architecture/02_agent.md` - Agent service architecture details
- `docs/STYLE.md` - Documentation style guide

**Core Architecture:**
- `services/agent/config/tools.yaml` - Tool registration
- `services/agent/src/core/db/models.py` - Database models
- `services/agent/src/core/context/service.py` - ContextService (shared context resolution)
- `services/agent/src/core/runtime/service.py` - AgentService (main runtime orchestrator)
- `services/agent/src/core/runtime/persistence.py` - DB CRUD (conversations, sessions, messages)
- `services/agent/src/interfaces/scheduler/adapter.py` - SchedulerAdapter (cron-based jobs)
- `services/agent/src/core/runtime/hitl.py` - Human-in-the-loop coordinator
- `services/agent/src/core/runtime/config.py` - RuntimeConfig (all runtime settings)
- `services/agent/src/core/agents/planner.py` - PlannerAgent (generates plans)
- `services/agent/src/core/agents/executor.py` - StepExecutorAgent (runs plan steps)
- `services/agent/src/core/skills/registry.py` - Skill validation
- `services/agent/src/core/skills/executor.py` - SkillExecutor (scoped execution)
- `services/agent/src/core/auth/credential_service.py` - CredentialService (context-scoped)
- `services/agent/src/shared/models.py` - Shared Pydantic models (StepOutcome, etc.)
- `services/agent/src/shared/chunk_filter.py` - ChunkFilter (verbosity + safety filtering)
- `services/agent/src/interfaces/base.py` - PlatformAdapter ABC

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
├── admin_portal.py           # Main dashboard
├── admin_api.py              # Diagnostic API (machine-readable endpoints)
├── admin_contexts.py         # Context management (primary management unit)
├── admin_diagnostics.py      # Diagnostics dashboard
├── admin_mcp.py              # MCP server management (user-defined connections)
├── admin_oauth.py            # OAuth provider management
├── admin_permissions.py      # Tool permissions per context
├── admin_price_tracker.py    # Price tracker management
├── admin_scheduler.py        # Scheduled job management
├── admin_users.py            # User management
├── admin_workspaces.py       # Git repository workspaces
└── templates/                # Large HTML templates (40KB+)
    ├── admin_context_detail.html  # Context detail with tabbed sub-views
    ├── admin_mcp.html             # MCP server management
    └── admin_price_tracker.html   # Price tracker dashboard
```

### Navigation System

Navigation is defined in `admin_shared.py`:

```python
# Actual navigation items from admin_shared.py
ADMIN_NAV_ITEMS: list[NavItem] = [
    NavItem("Dashboard", "/platformadmin/", "&#127968;", "home"),
    NavItem("Diagnostics", "/platformadmin/diagnostics/", "&#128200;", "monitoring"),
    NavItem("Users", "/platformadmin/users/", "&#128100;", "users"),
    NavItem("Contexts", "/platformadmin/contexts/", "&#128451;", "users"),
    NavItem("Scheduler", "/platformadmin/scheduler/", "&#128339;", "features"),
    NavItem("Price Tracker", "/platformadmin/price-tracker/", "&#128181;", "features"),
    NavItem("Chat", "/", "&#128172;", "external"),
    NavItem("Open WebUI Admin", "/admin/", "&#128279;", "external"),
]
```

### Adding a New Admin Module

1. **Create the router file** (`interfaces/http/admin_mymodule.py`):

```python
from fastapi import APIRouter, Depends
from interfaces.http.admin_shared import admin_page_layout, ADMIN_NAV_ITEMS

router = APIRouter(prefix="/platformadmin/mymodule", tags=["admin-mymodule"])

@router.get("/")
async def mymodule_dashboard():
    content = "<h2>My Module</h2>..."
    return HTMLResponse(admin_page_layout("My Module", content, ADMIN_NAV_ITEMS))
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

### MCP Server Management

The MCP management page (`/platformadmin/mcp/`) allows admins to configure user-defined MCP server connections per context.

**Features:**
- Add/edit/delete MCP servers with name, URL, transport (auto/SSE/streamable HTTP)
- Authentication: None, Bearer token (encrypted), OAuth 2.0/2.1 (with PKCE)
- Test connection button with tool discovery
- Connection status monitoring (pending/connected/error)
- OAuth authorization flow for servers requiring OAuth
- All credentials encrypted at rest (Fernet)

**API Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/platformadmin/mcp/servers` | GET | List all MCP servers |
| `/platformadmin/mcp/servers` | POST | Create new MCP server |
| `/platformadmin/mcp/servers/{id}` | PUT | Update MCP server |
| `/platformadmin/mcp/servers/{id}` | DELETE | Delete MCP server |
| `/platformadmin/mcp/servers/{id}/test` | POST | Test connection |
| `/platformadmin/mcp/servers/{id}/oauth/start` | POST | Start OAuth flow |

**Template:** `interfaces/http/templates/admin_mcp.html`

---

## Diagnostic API (For AI/Programmatic Access)

The Diagnostic API (`/platformadmin/api/`) provides machine-readable endpoints for AI agents and scripts to diagnose the platform without browser-based Entra ID authentication.

### Authentication

Two authentication methods are supported:

1. **X-API-Key header** (preferred for AI/scripts):
   ```bash
   curl -H "X-API-Key: $AGENT_DIAGNOSTIC_API_KEY" \
     https://your-domain/platformadmin/api/status
   ```

2. **Entra ID session** (for browser access - same as admin portal)

**Environment variable:** `AGENT_DIAGNOSTIC_API_KEY`
Generate with: `openssl rand -hex 32`

### Available Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /platformadmin/api/status` | System health status (HEALTHY/DEGRADED/CRITICAL) |
| `GET /platformadmin/api/otel-metrics` | Live OTel metrics with computed insights (error rate, avg latency, token usage) |
| `GET /platformadmin/api/investigate/{trace_id}` | Unified view: trace spans + debug logs + summary for one request |
| `GET /platformadmin/api/debug/logs` | Query debug log entries (filter by trace_id, event_type) |
| `GET /platformadmin/api/debug/stats` | Debug log statistics by event type |
| `GET /platformadmin/api/traces/search` | Search OpenTelemetry traces (filter by status, duration, trace_id) |
| `GET /platformadmin/api/traces/{trace_id}` | Get full trace detail with all spans |
| `GET /platformadmin/api/conversations` | List conversations with message counts |
| `GET /platformadmin/api/conversations/{id}/messages` | Get messages for a conversation |
| `GET /platformadmin/api/tools/stats` | Tool execution statistics |
| `GET /platformadmin/api/skills/stats` | Skill execution statistics |
| `GET /platformadmin/api/requests/stats` | HTTP request timing statistics |
| `GET /platformadmin/api/config` | Get system configuration entries |
| `GET /platformadmin/api/health` | Simple health check (no auth) |

### Troubleshooting Workflow

When diagnosing an issue, follow this sequence:

**Step 1: Check overall health**
```bash
curl -H "X-Api-Key: $KEY" $BASE/status
```
Look at `status` (HEALTHY/DEGRADED/CRITICAL), `recent_errors`, and `recommended_actions`.

**Step 2: Check metrics for anomalies**
```bash
curl -H "X-Api-Key: $KEY" $BASE/otel-metrics
```
Key fields in `insights`:
- `error_rate_pct` > 5% indicates a problem
- `avg_request_duration_ms` > 30000 indicates slowness
- `total_tool_errors` > 0 indicates tool failures

**Step 3: Find error traces**
```bash
curl -H "X-Api-Key: $KEY" "$BASE/traces/search?status=ERR&limit=10"
```
Returns recent error traces with trace_id, name, duration, and start_time.

**Step 4: Investigate a specific trace**
```bash
curl -H "X-Api-Key: $KEY" $BASE/investigate/{trace_id}
```
Returns everything for that request in one call:
- `spans`: All trace spans (timing, status, attributes)
- `debug_logs`: All debug events (LLM prompts, tool calls, supervisor decisions)
- `summary`: Computed overview (duration, error count, tools used, outcome)

**Step 5: Deep-dive into debug logs**
```bash
# All debug events for a trace
curl -H "X-Api-Key: $KEY" "$BASE/debug/logs?trace_id={trace_id}"

# Only supervisor decisions (to find ABORT/REPLAN)
curl -H "X-Api-Key: $KEY" "$BASE/debug/logs?event_type=supervisor&limit=20"

# Only tool calls (to find failures)
curl -H "X-Api-Key: $KEY" "$BASE/debug/logs?event_type=tool_call&limit=20"
```

### Quick Reference for Claude Code Sessions

When troubleshooting the live platform from a Claude Code session:
```bash
# Set up (once per session) -- all access via Traefik (no direct host ports)
KEY=$(grep AGENT_DIAGNOSTIC_API_KEY .env | cut -d= -f2)
BASE_DEV="https://agent-dev.falle.se/platformadmin/api"
BASE_PROD="https://agent.falle.se/platformadmin/api"
BASE=$BASE_DEV  # Default to dev

# Health check
curl -s -H "X-Api-Key: $KEY" $BASE/status | python -m json.tool

# Is something broken? Check error rate
curl -s -H "X-Api-Key: $KEY" $BASE/otel-metrics | python -m json.tool

# Find recent failures
curl -s -H "X-Api-Key: $KEY" "$BASE/traces/search?status=ERR&limit=5" | python -m json.tool

# Full investigation of a specific request
curl -s -H "X-Api-Key: $KEY" "$BASE/investigate/TRACE_ID_HERE" | python -m json.tool
```

### Implementation

- **Router:** `services/agent/src/interfaces/http/admin_api.py`
- **Config:** `AGENT_DIAGNOSTIC_API_KEY` in `.env`
- Uses `DiagnosticsService` for health checks and trace analysis
- OTel metrics stored in-memory via `core/observability/metrics.py` (snapshot dict)
- Debug logs stored in `data/debug_logs.jsonl` (JSONL with rotation, not DB)

---

## Testing Guidelines

### Testing Strategy (3-Layer Pyramid)

- **Layer 1: Unit Tests** (fast, mocked) -- Use `MockLLMClient` and `InMemoryAsyncSession` from `core/tests/mocks.py`
- **Layer 2: Integration Tests** (real DB, mocked LLM) -- Full request flows
- **Layer 3: Semantic Tests** (slow, real LLM) -- Golden queries in `tests/semantic/golden_queries.yaml`

Every feature flow needs a scenario test in `src/core/tests/test_agent_scenarios.py`.

### Test Structure

New tests go in `src/*/tests/` near source code (NOT in `tests/` root dirs).

```
services/agent/
├── src/
│   ├── core/
│   │   ├── tests/            # Core unit tests (37+ files, 890+ tests)
│   │   │   ├── mocks.py              # MockLLMClient, test fixtures
│   │   │   ├── test_planner_agent.py  # PlannerAgent tests
│   │   │   ├── test_executor_agent.py # StepExecutorAgent tests
│   │   │   ├── test_service.py        # AgentService tests
│   │   │   ├── test_skill_executor.py # SkillExecutor tests
│   │   │   └── ...
│   │   └── observability/
│   │       └── tests/         # Observability tests
│   │           └── test_span_rotation.py
│   └── stack/
│       └── tests/             # Stack CLI tests
└── tests/                     # Legacy integration tests (NOT for new tests)
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
3. **For git/test/deploy:** Use `/ops` - ALWAYS delegate these operations
4. **For simple repetitive tasks:** Delegate to Haiku via `Task(subagent_type="simple-tasks", model="haiku", ...)`
5. **For trivial one-off fixes:** Do directly (1-2 edits max)

---

**Remember:**
- The workflow is optimized for autonomy and cost-efficiency. Use the right agent for each task.
- Platform skills in `skills/` are the application's domain logic (not instructions for Claude Code).
- Claude Code agent configs live in `.claude/agents/` (separate from platform skills).
- Surgical edits: preserve comments and existing functionality unless explicitly asked to change.
