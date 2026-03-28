---
focus: arch
generated: 2026-03-28
---

# Codebase Structure

**Analysis Date:** 2026-03-28

## Directory Layout

```
ai-agent-platform/                  # Project root
├── services/
│   ├── agent/                      # Primary Python service (FastAPI)
│   │   ├── src/                    # All Python source code
│   │   │   ├── core/               # Execution runtime, DB, tools, skills
│   │   │   ├── interfaces/         # HTTP/Telegram/Scheduler adapters
│   │   │   ├── modules/            # Isolated feature modules (RAG, email, etc.)
│   │   │   ├── orchestrator/       # Dispatcher and routing
│   │   │   ├── shared/             # Cross-layer Pydantic models and utilities
│   │   │   ├── stack/              # Stack CLI implementation
│   │   │   └── utils/              # Generic utility functions
│   │   ├── config/
│   │   │   ├── tools.yaml          # Tool registration (name, type, enabled, description)
│   │   │   └── models.yaml         # LLM model configuration
│   │   ├── alembic/                # Database migrations
│   │   │   └── versions/           # Individual migration scripts
│   │   ├── alembic.ini             # Alembic configuration
│   │   ├── pyproject.toml          # Poetry dependencies and tool config
│   │   ├── Dockerfile              # Agent service container
│   │   └── entrypoint.sh           # Container entrypoint
│   └── litellm/                    # LiteLLM proxy service
│       └── searxng/                # SearXNG search service
├── skills/                         # Skill Markdown files (mounted to /app/skills)
│   ├── general/                    # General-purpose skills
│   ├── work/                       # Work/business skills
│   ├── development/                # Development tools skills
│   └── system/                     # System/internal skills
├── docs/                           # Architecture and style documentation
│   ├── ARCHITECTURE.md             # Architecture overview
│   └── architecture/               # Detailed subsystem docs
├── tests/                          # Legacy integration tests (NOT for new tests)
├── contexts/                       # Context overlay files (per-context skill overrides)
├── data/                           # Runtime data (spans.jsonl, debug_logs.jsonl)
├── scripts/                        # Operational scripts
├── capabilities/                   # Capability definitions
├── docker-compose.yml              # Base compose file
├── docker-compose.dev.yml          # Dev environment overrides
├── docker-compose.prod.yml         # Production overrides
├── docker-compose.override.yml     # Local overrides (git-ignored)
├── stack                           # Stack CLI entry point (bash)
├── stack_cli_wrapper.py            # Python CLI wrapper
├── CLAUDE.md                       # Claude Code session instructions
└── .architecture-baseline.json     # Architecture rule enforcement baseline
```

## services/agent/src/ Breakdown

### core/

```
core/
├── agents/                         # LLM agent implementations
│   ├── planner.py                  # PlannerAgent (generates Plans from user prompts)
│   ├── executor.py                 # StepExecutorAgent (runs individual PlanSteps)
│   ├── supervisor_step.py          # StepSupervisorAgent (evaluates StepOutcome)
│   ├── supervisor_plan.py          # PlanSupervisorAgent (validates generated plans)
│   └── response_agent.py          # ResponseAgent (final response assembly)
├── auth/                           # Authentication and credentials
│   ├── credential_service.py       # CredentialService (Fernet-encrypted, context-scoped)
│   ├── header_auth.py              # X-OpenWebUI-User-* header parsing
│   ├── user_service.py             # User auto-provisioning
│   ├── token_manager.py            # OAuth token management
│   ├── oauth_client.py             # OAuth 2.0 client
│   └── admin_session.py            # Admin session handling
├── context/                        # Context resolution
│   ├── service.py                  # ContextService (resolve_for_authenticated_user, etc.)
│   └── files.py                    # Context file utilities
├── db/                             # Database layer
│   ├── models.py                   # All SQLAlchemy ORM models
│   ├── oauth_models.py             # OAuth token models + encrypt/decrypt helpers
│   ├── engine.py                   # Async SQLAlchemy engine + session factory
│   └── retention.py                # Data retention policies
├── diagnostics/                    # Diagnostics service
├── mcp/                            # Model Context Protocol client
├── middleware/                     # FastAPI middleware (rate limiting, etc.)
├── models/                         # Internal Pydantic schemas (not shared)
│   └── pydantic_schemas.py         # TraceContext, PlanEvent, StepEvent, etc.
├── observability/                  # Tracing and monitoring
│   ├── tracing.py                  # OTel span management, size-based rotation
│   ├── debug_logger.py             # Debug events -> data/debug_logs.jsonl
│   ├── logging.py                  # Logging setup + OTel log bridge
│   ├── metrics.py                  # OTel MeterProvider + in-memory snapshot
│   ├── error_codes.py              # Structured error codes (TOOL_*, LLM_*, etc.)
│   └── security_logger.py          # Security event logging
├── protocols/                      # Protocol (interface) definitions
├── routing/                        # Request routing logic
│   ├── unified_orchestrator.py     # UnifiedOrchestrator
│   └── guidance.py                 # Routing guidance
├── runtime/                        # Agent service runtime (decomposed from AgentService)
│   ├── service.py                  # AgentService (main orchestration entry point)
│   ├── service_factory.py          # ServiceFactory (per-request isolation)
│   ├── persistence.py              # ConversationPersistence (DB CRUD)
│   ├── context_injector.py         # ContextInjector (pinned files, workspace rules)
│   ├── tool_runner.py              # ToolRunner (tool invocation + schema generation)
│   ├── hitl.py                     # HITLCoordinator (human-in-the-loop)
│   ├── config.py                   # Settings / RuntimeConfig (all env-based settings)
│   ├── litellm_client.py           # LiteLLMClient wrapper
│   ├── memory.py                   # MemoryStore (Qdrant vector store)
│   ├── model_registry.py           # Model registry
│   ├── routing.py                  # Router registry
│   └── skill_quality.py            # SkillQualityAnalyser + evaluate_conversation_quality
├── skills/                         # Skill infrastructure
│   ├── registry.py                 # SkillRegistry (startup validation, protocol)
│   ├── executor.py                 # SkillExecutor (scoped tool execution)
│   └── composite.py                # CompositeSkillRegistry
├── tools/                          # Tool implementations
│   ├── base.py                     # Tool ABC, ToolConfirmationError
│   ├── registry.py                 # ToolRegistry
│   ├── loader.py                   # Tool loader from config
│   ├── mcp_loader.py               # MCP tool loader
│   ├── activity_hints.py           # User-facing activity messages
│   ├── azure_devops.py             # Azure DevOps work items (context-scoped)
│   ├── claude_code.py              # Claude Code subprocess wrapper
│   ├── filesystem.py               # Filesystem operations
│   ├── git_clone.py                # Git repo cloning to workspace
│   ├── github_pr.py                # GitHub PR creation via gh CLI
│   ├── homey.py                    # Homey smart home control
│   ├── memory_writer.py            # Write to Qdrant memory
│   ├── price_tracker.py            # Price tracking tool
│   ├── send_email.py               # Email sending
│   ├── semantic_eval.py            # Semantic evaluation runner
│   ├── test_runner.py              # Test execution tool
│   ├── tibp_wiki_search.py         # Wiki search
│   ├── vault.py                    # Secrets vault
│   ├── web_fetch.py                # Web page fetching (SSRF-protected)
│   ├── web_search.py               # Web search via SearXNG
│   └── wiki_sync.py                # ADO wiki sync
├── utils/                          # Core-level utilities
├── validators/                     # Input validators
├── wiki/                           # Wiki-related helpers
├── command_loader.py               # Skill/command file loading
├── context_manager.py              # ContextManager (conversation naming)
├── providers.py                    # Runtime provider factories
└── system_commands.py              # System command handlers (/help, etc.)
```

### interfaces/

```
interfaces/
├── base.py                         # PlatformAdapter ABC (platform_name, start, stop, send_message)
├── http/                           # HTTP/FastAPI interface
│   ├── app.py                      # FastAPI app factory; registers all routers
│   ├── bootstrap.py                # Lifespan: DB init, tool/skill registration, seeding
│   ├── agent_api.py                # Agent endpoint (POST /v1/agent, SSE streaming)
│   ├── openwebui_adapter.py        # OpenAI-compatible endpoint for Open WebUI
│   ├── admin_portal.py             # Main dashboard (/platformadmin/)
│   ├── admin_api.py                # Diagnostic API (/platformadmin/api/)
│   ├── admin_ado.py                # Azure DevOps admin
│   ├── admin_auth.py               # Auth for admin portal
│   ├── admin_auth_oauth.py         # OAuth auth flow
│   ├── admin_contexts.py           # Context management + Skill Quality tab
│   ├── admin_diagnostics.py        # Diagnostics dashboard
│   ├── admin_mcp.py                # MCP server management
│   ├── admin_oauth.py              # OAuth provider management
│   ├── admin_permissions.py        # Tool permissions per context
│   ├── admin_price_tracker.py      # Price tracker management
│   ├── admin_scheduler.py          # Scheduled job management
│   ├── admin_shared.py             # Navigation, shared layout components
│   ├── admin_users.py              # User management
│   ├── admin_wiki.py               # Wiki import management
│   ├── admin_workspaces.py         # Git workspace management
│   ├── csrf.py                     # CSRF protection
│   ├── dependencies.py             # FastAPI dependency injection helpers
│   ├── middleware.py               # Middleware registration (CSP, CORS, rate limit)
│   ├── oauth.py                    # OAuth provider routes
│   ├── oauth_webui.py              # Open WebUI OAuth integration
│   ├── readiness.py                # Health/readiness probe endpoints
│   ├── schemas/                    # Request/response Pydantic schemas
│   └── templates/                  # HTML templates for admin portal (40KB+ files)
│       ├── admin_context_detail.html
│       ├── admin_mcp.html
│       └── admin_price_tracker.html
├── scheduler/
│   └── adapter.py                  # SchedulerAdapter (croniter, in-process asyncio, 60s interval)
└── telegram/
    └── adapter.py                  # TelegramAdapter (ContextService + ChunkFilter)
```

### modules/

```
modules/
├── rag/                            # Retrieval-augmented generation
├── indexer/                        # Document indexing
├── embedder/                       # Text embedding
├── fetcher/                        # Web content fetching (SSRF-protected)
├── homey/                          # Homey smart home module
├── email/                          # Email module
└── price_tracker/                  # Price tracking module
```

### shared/

```
shared/
├── models.py                       # AgentRequest, AgentResponse, Plan, PlanStep, StepOutcome, etc.
├── streaming.py                    # AgentChunk (streaming unit)
├── chunk_filter.py                 # ChunkFilter (verbosity + safety filtering)
├── content_classifier.py           # Content classification (centralized)
└── sanitize.py                     # Input sanitization helpers
```

### orchestrator/

```
orchestrator/
├── dispatcher.py                   # Dispatcher (routes messages to AgentService)
├── startup.py                      # Orchestrator startup logic
└── price_tracker.py                # Price tracker orchestration
```

## skills/ Directory Structure

```
skills/
├── general/                        # General-purpose user-facing skills
│   ├── researcher.md               # Web research (web_search, fetch_url)
│   ├── deep_researcher.md          # Comprehensive multi-source research
│   ├── price_tracker.md            # Price tracking (Prisjakt)
│   ├── homey.md                    # Smart home control
│   ├── web_searcher.md             # Direct web search
│   └── obsidian_vault.md          # Obsidian vault interaction
├── work/                           # Work/business skills
│   ├── backlog_manager.md          # Azure DevOps backlog management
│   ├── requirements_drafter.md     # Requirements drafting
│   ├── requirements_writer.md      # Requirements writing
│   └── tibp_researcher.md          # TIBP-specific research
├── development/                    # Development workflow skills
│   └── software_engineer.md        # Code investigation/fixes via Claude Code
└── system/                         # Internal/system skills
    ├── system_eval.md              # System evaluation
    └── wiki_sync.md                # ADO wiki sync
```

Skill files follow this frontmatter format:
```yaml
---
name: "skill_name"
description: "Shown to Planner for routing decisions"
tools: ["tool1", "tool2"]   # Scoped tool access (only these tools available)
model: agentchat             # agentchat or skillsrunner
max_turns: 5                 # Max tool-calling iterations
---
```

## Key File Locations

**Entry Points:**
- `services/agent/src/interfaces/http/app.py`: FastAPI app factory
- `services/agent/src/interfaces/http/bootstrap.py`: Lifespan (startup/shutdown)
- `services/agent/src/interfaces/http/agent_api.py`: Primary agent endpoint
- `services/agent/src/interfaces/http/openwebui_adapter.py`: OpenAI-compatible endpoint

**Core Orchestration:**
- `services/agent/src/core/runtime/service.py`: AgentService (main orchestrator)
- `services/agent/src/core/runtime/service_factory.py`: ServiceFactory (per-request isolation)
- `services/agent/src/core/agents/planner.py`: PlannerAgent
- `services/agent/src/core/agents/executor.py`: StepExecutorAgent
- `services/agent/src/core/agents/supervisor_step.py`: StepSupervisorAgent
- `services/agent/src/core/skills/executor.py`: SkillExecutor
- `services/agent/src/core/skills/registry.py`: SkillRegistry

**Database:**
- `services/agent/src/core/db/models.py`: All SQLAlchemy ORM models
- `services/agent/src/core/db/oauth_models.py`: OAuth token models
- `services/agent/src/core/db/engine.py`: Async engine + session factory
- `services/agent/alembic/versions/`: Migration scripts

**Shared Domain Models:**
- `services/agent/src/shared/models.py`: AgentRequest, Plan, PlanStep, StepOutcome, etc.
- `services/agent/src/shared/streaming.py`: AgentChunk streaming type
- `services/agent/src/shared/chunk_filter.py`: ChunkFilter

**Configuration:**
- `services/agent/src/core/runtime/config.py`: Settings (all env-based runtime config)
- `services/agent/config/tools.yaml`: Tool registration
- `services/agent/config/models.yaml`: LLM model configuration
- `services/agent/pyproject.toml`: Dependencies (Poetry)
- `docker-compose.yml` / `docker-compose.dev.yml` / `docker-compose.prod.yml`: Infrastructure

**Admin Portal:**
- `services/agent/src/interfaces/http/admin_shared.py`: Navigation items, shared layout
- `services/agent/src/interfaces/http/admin_contexts.py`: Primary context management
- `services/agent/src/interfaces/http/admin_api.py`: Diagnostic API (machine-readable)
- `services/agent/src/interfaces/http/templates/`: Large HTML templates (>40KB)

**Observability:**
- `services/agent/src/core/observability/tracing.py`: OTel spans
- `services/agent/src/core/observability/debug_logger.py`: Debug events to JSONL
- `services/agent/src/core/observability/error_codes.py`: Structured error codes
- `data/debug_logs.jsonl`: Runtime debug event log (JSONL)

**Testing:**
- `services/agent/src/core/tests/`: Unit tests (37+ files, 920+ tests) -- new tests go here
- `services/agent/src/core/tests/mocks.py`: MockLLMClient, InMemoryAsyncSession, fixtures
- `services/agent/src/core/tests/test_agent_scenarios.py`: End-to-end scenario tests
- `tests/semantic/golden_queries.yaml`: Golden queries for semantic tests

## Naming Conventions

**Files:**
- Python modules: `snake_case.py`
- Admin portal modules: `admin_<feature>.py`
- Test files: `test_<module>.py` co-located in `src/*/tests/`
- Skill files: `<skill_name>.md` (lowercase, underscores)

**Directories:**
- Python packages: `snake_case/`
- Feature modules: named by domain (`rag/`, `indexer/`, `homey/`)

## Where to Add New Code

**New Tool:**
1. Implementation: `services/agent/src/core/tools/<tool_name>.py` (subclass `Tool` from `core/tools/base.py`)
2. Registration: add entry to `services/agent/config/tools.yaml`
3. Tests: `services/agent/src/core/tools/tests/test_<tool_name>.py`
4. If needs context injection: update `core/runtime/service.py` `_execute_step()` injection block

**New Skill:**
1. Create: `skills/<category>/<skill_name>.md` with valid YAML frontmatter
2. Tools listed in `tools:` must already exist in `ToolRegistry`
3. `SkillRegistry` validates at startup; check logs if skill not appearing

**New Feature Module:**
1. Create: `services/agent/src/modules/<feature_name>/` package
2. Rule: only import from `core/`; never import other modules

**New Admin Portal Page:**
1. Router: `services/agent/src/interfaces/http/admin_<feature>.py`
2. Register router in `services/agent/src/interfaces/http/app.py`
3. Add `NavItem` to `ADMIN_NAV_ITEMS` in `services/agent/src/interfaces/http/admin_shared.py`
4. Large HTML (>500 lines or >40KB): extract to `services/agent/src/interfaces/http/templates/admin_<feature>.html`

**New API Endpoint:**
1. Add to existing router in `services/agent/src/interfaces/http/` or create new router
2. Register in `app.py` if new router
3. Shared schemas: `services/agent/src/interfaces/http/schemas/`

**New Database Model:**
1. Add to `services/agent/src/core/db/models.py`
2. Create migration: `cd services/agent && alembic revision --autogenerate -m "<description>"`
3. Revision ID must be <=32 characters (PostgreSQL varchar(32) limit)

**New Unit Test:**
1. Location: `services/agent/src/core/tests/test_<module>.py` (NOT in `tests/` root)
2. Use `MockLLMClient` and `InMemoryAsyncSession` from `core/tests/mocks.py`
3. Mark async tests with `@pytest.mark.asyncio`

## Special Directories

**`data/`:**
- Purpose: Runtime data files (debug logs, OTel spans)
- Files: `debug_logs.jsonl` (debug events), `spans.jsonl` (OTel spans with rotation)
- Generated: Yes (at runtime)
- Committed: No

**`contexts/`:**
- Purpose: Per-context skill overlays (self-healing skill improvements)
- Generated: Yes (by SkillQualityAnalyser)
- Committed: No (runtime state)

**`services/agent/alembic/versions/`:**
- Purpose: Database schema migration scripts
- Generated: Semi (autogenerated then manually reviewed)
- Committed: Yes (source files)

**`.planning/`:**
- Purpose: GSD planning documents and codebase analysis
- Committed: Yes

**`skills/`:**
- Purpose: Skill Markdown files mounted into container at `/app/skills`
- Committed: Yes (source files)
- Loaded: At startup by `SkillRegistry`; changes require service restart

---

*Structure analysis: 2026-03-28*
