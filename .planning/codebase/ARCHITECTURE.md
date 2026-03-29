---
focus: arch
generated: 2026-03-28
---

# Architecture

**Analysis Date:** 2026-03-28

## Pattern Overview

**Overall:** 4-layer modular monolith with strict unidirectional dependency rules

**Key Characteristics:**
- Layers depend only downward: `interfaces/ -> orchestrator/ -> core/` (modules is parallel to orchestrator)
- `core/` never imports upward; it exposes Protocol interfaces for dependency injection
- All I/O is async-first throughout the stack
- Multi-tenant: every resource is scoped to a `context_id` (UUID)
- Skills-native execution: Markdown files with YAML frontmatter are the primary execution unit
- Self-correction via 4-level `StepOutcome` system (SUCCESS/RETRY/REPLAN/ABORT)

## Layers

**interfaces/ (Top Layer):**
- Purpose: Adapts external protocols to internal data structures; no business logic
- Location: `services/agent/src/interfaces/`
- Contains: HTTP/FastAPI routers, OpenWebUI adapter, Telegram adapter, Scheduler adapter, Admin portal modules
- Depends on: `orchestrator/`, `core/`
- Used by: External clients (Open WebUI, Telegram, CLI)
- Entry: `interfaces/http/app.py` (FastAPI app factory), `interfaces/http/bootstrap.py` (lifespan)

**orchestrator/ (Workflow Layer):**
- Purpose: Request routing and dispatch; delegates to AgentService via Dispatcher
- Location: `services/agent/src/orchestrator/`
- Contains: `dispatcher.py` (routes messages), `startup.py`, `price_tracker.py`
- Depends on: `core/`
- Used by: `interfaces/`

**modules/ (Feature Isolation Layer):**
- Purpose: Isolated domain features (RAG, indexer, embedder, email, price tracker, etc.)
- Location: `services/agent/src/modules/`
- Contains: `rag/`, `indexer/`, `embedder/`, `fetcher/`, `homey/`, `email/`, `price_tracker/`
- Rule: Can ONLY import `core/`. Cannot import other modules or `orchestrator/`
- Used by: Tools in `core/tools/` that delegate to module functionality

**core/ (Bottom Layer):**
- Purpose: Execution runtime, database models, LLM clients, tools, skills, observability
- Location: `services/agent/src/core/`
- Contains: Agents, tools, skills, runtime, db, auth, observability, context management
- Rule: NEVER imports from `interfaces/`, `orchestrator/`, or `modules/`

**shared/ (Cross-cutting):**
- Purpose: Domain models shared by all layers (Pydantic schemas, streaming types)
- Location: `services/agent/src/shared/`
- Contains: `models.py` (AgentRequest, Plan, PlanStep, StepOutcome, etc.), `chunk_filter.py`, `streaming.py`, `content_classifier.py`
- Rule: Imported by all layers; imports nothing from the project

## Key Components

**AgentService** (`core/runtime/service.py`):
- Main orchestration entry point for all agent requests
- Coordinates: `PlannerAgent`, `StepExecutorAgent`, `SkillExecutor`, supervisors, persistence, HITL
- Decomposes into sub-modules: `ConversationPersistence`, `ContextInjector`, `ToolRunner`, `HITLCoordinator`
- Initialized per-request via `ServiceFactory` (not a singleton)

**PlannerAgent** (`core/agents/planner.py`):
- Generates structured `Plan` (list of `PlanStep`) from user prompt
- Uses `model_planner` LLM setting
- Sanitizes user input to prevent prompt injection
- Returns `RoutingDecision`: FAST_PATH, CHAT, or AGENTIC

**PlanSupervisorAgent** (`core/agents/supervisor_plan.py`):
- Validates the plan before execution begins
- Checks tool/skill availability, enforces planning constraints

**StepExecutorAgent** (`core/agents/executor.py`):
- Executes individual `PlanStep` objects
- Handles: memory search steps, tool execution steps, LLM completion steps
- 120-second default timeout per tool call

**StepSupervisorAgent** (`core/agents/supervisor_step.py`):
- Evaluates each step result and returns `StepOutcome`
- Uses `model_supervisor` LLM setting

**SkillExecutor** (`core/skills/executor.py`):
- Primary execution path for skill-type plan steps
- Enforces strict tool scoping: skill can only access tools listed in its frontmatter
- Supports streaming, rate limiting, deduplication, retry feedback

**SkillRegistry** (`core/skills/registry.py`):
- Validates all skill `.md` files at startup
- Checks that tool references in frontmatter exist in `ToolRegistry`
- Protocol-based: `SkillRegistryProtocol` allows testable injection

**ToolRegistry** (`core/tools/registry.py`):
- Registers and looks up tool implementations
- Base registry cloned per request with per-context permissions applied
- MCP tools loaded dynamically per context OAuth

**Dispatcher** (`orchestrator/dispatcher.py`):
- Routes incoming messages from platform adapters
- Wraps `AgentService.execute_stream()` and `UnifiedOrchestrator`
- Converts raw messages to `AgentRequest`, forwards to AgentService

**ContextService** (`core/context/service.py`):
- Consolidates context resolution for all adapters
- Methods: `resolve_for_authenticated_user()`, `resolve_for_platform()`, `resolve_for_conversation_id()`, `resolve_anonymous()`

**ChunkFilter** (`shared/chunk_filter.py`):
- Applied by adapters to `AgentChunk` streams before sending to users
- Controls verbosity (DEFAULT/VERBOSE/DEBUG), filters raw model tokens, deduplicates plan descriptions

**HITLCoordinator** (`core/runtime/hitl.py`):
- Human-in-the-loop workflow coordinator
- Manages `AwaitingInputRequest` and paused skill execution state

**ConversationPersistence** (`core/runtime/persistence.py`):
- All DB CRUD for conversations, sessions, messages
- Used by `AgentService`

**ContextInjector** (`core/runtime/context_injector.py`):
- Injects pinned files and workspace rules into conversation history
- Security: validates file paths before injection

**SkillQualityAnalyser** (`core/runtime/skill_quality.py`):
- End-of-conversation quality evaluator
- Scores skills 1-5, writes `SkillQualityRating` records
- Triggers `SkillImprovementProposal` generation when scores drop (self-healing)

## Data Flow

**Agentic Request (primary path):**

1. Platform client (Open WebUI, Telegram) sends request to `interfaces/http/openwebui_adapter.py` or `interfaces/telegram/adapter.py`
2. Adapter resolves context via `ContextService` (`core/context/service.py`)
3. Adapter calls `Dispatcher.stream_message()` (`orchestrator/dispatcher.py`)
4. Dispatcher calls `AgentService.execute_stream()` (`core/runtime/service.py`)
5. `AgentService` sets up Conversation/Session hierarchy in PostgreSQL via `ConversationPersistence`
6. `AgentService` loads history, injects pinned files and workspace rules via `ContextInjector`
7. `PlannerAgent` generates a `Plan` (list of `PlanStep`) via LLM call
8. `PlanSupervisorAgent` validates the plan
9. For each `PlanStep`:
   a. If `executor=="skill"`: `SkillExecutor` runs the skill with scoped tools
   b. Else: `StepExecutorAgent` runs the step (memory, tool, or LLM completion)
   c. `StepSupervisorAgent` evaluates result, returns `StepOutcome`
   d. On RETRY: re-execute with feedback (max 1 retry)
   e. On REPLAN: generate new plan (max 3 replans)
   f. On ABORT: stop execution
10. Results streamed as `AgentChunk` events back to adapter
11. Adapter applies `ChunkFilter` before forwarding to platform
12. `AgentService` persists messages, triggers background memory write (Qdrant)
13. Post-conversation: `SkillQualityAnalyser` evaluates and records quality ratings

**Direct Chat (non-agentic path):**
- Routed when `RoutingDecision.CHAT` is returned
- `AgentService._route_chat_request()` calls `LiteLLMClient.generate()` directly
- No planning or skill execution

**State Management:**
- All state persisted in PostgreSQL via SQLAlchemy 2.0 async
- Vector memory in Qdrant (per-context filtered)
- Streaming state passed via Python `AsyncGenerator` chains

## State Hierarchy

```
Context (UUID)                # Multi-tenant workspace (user or shared)
  └── Conversation (UUID)     # Chat thread, linked to platform + platform_id
        └── Session (UUID)    # Single request/response cycle
              └── Message (UUID)  # Individual chat message (user/assistant/system/tool)
```

**Context** (`core/db/models.py:Context`):
- Primary isolation unit; all data scoped to `context_id`
- Has `pinned_files` (injected into every prompt), `default_cwd`, `config` (JSONB), `type`
- Owns: Conversations, OAuthTokens, ToolPermissions, ScheduledJobs, Workspaces, Credentials

**Conversation** (`core/db/models.py:Conversation`):
- Tracks `platform` + `platform_id` (e.g., `openwebui` + chat UUID)
- Holds `current_cwd` for tool execution directory state
- `conversation_metadata` JSONB stores pending HITL state

**Session** (`core/db/models.py:Session`):
- Groups messages for a single agent request cycle
- `active` flag; `session_metadata` JSONB

**Message** (`core/db/models.py:Message`):
- Roles: `user`, `assistant`, `system`, `tool`
- `trace_id` links to OpenTelemetry trace for debugging

## Database Models

All models in `services/agent/src/core/db/models.py`:

| Model | Table | Purpose |
|-------|-------|---------|
| `Context` | `contexts` | Multi-tenant workspace |
| `Conversation` | `conversations` | Chat thread per platform |
| `Session` | `sessions` | Per-request execution group |
| `Message` | `messages` | Individual chat message |
| `User` | `users` | User account (OpenWebUI identity) |
| `UserContext` | `user_contexts` | User <-> Context junction with role |
| `ToolPermission` | `tool_permissions` | Per-context tool allow/deny |
| `UserCredential` | `user_credentials` | Fernet-encrypted credentials (scoped to context) |
| `Workspace` | `workspaces` | Git repo per context |
| `McpServer` | `mcp_servers` | User-defined MCP server config |
| `ScheduledJob` | `scheduled_jobs` | Cron job definition per context |
| `HomeyDeviceCache` | `homey_device_cache` | Cached smart-home device metadata |
| `SkillQualityRating` | `skill_quality_ratings` | Per-conversation skill score (1-5) |
| `SkillImprovementProposal` | `skill_improvement_proposals` | AI-generated skill improvements |
| `SystemConfig` | `system_config` | Global key-value config (debug flags, etc.) |
| `AdoTeamConfig` | `ado_team_configs` | Azure DevOps team mapping |
| `WikiImport` | `wiki_imports` | ADO wiki import state per context |

OAuth models in `services/agent/src/core/db/oauth_models.py`:
- `OAuthToken`: encrypted OAuth tokens per context
- Encryption via Fernet (`encrypt_token()` / `decrypt_token()` with plaintext fallback)

## Key Design Patterns

**Protocol-Based Dependency Injection:**
- `core/` defines `Protocol` classes for interfaces (e.g., `SkillRegistryProtocol`)
- Concrete implementations injected at startup in `interfaces/http/bootstrap.py`
- Enables testing with `MockLLMClient`, `InMemoryAsyncSession` without touching `interfaces/`

**ServiceFactory Pattern:**
- `core/runtime/service_factory.py` creates isolated `AgentService` per request
- Each service gets: cloned tool registry (filtered by context permissions), context-filtered memory, OAuth-authenticated MCP clients
- Not a singleton; prevents cross-request state leakage

**Skill-Based Execution:**
- Skills defined as Markdown files (`skills/**/*.md`) with YAML frontmatter
- Frontmatter declares: `name`, `description`, `tools` (scoped list), `model`, `max_turns`
- `SkillRegistry` validates all skills at startup; invalid tool references log warnings
- `SkillExecutor` builds a scoped tool set containing only `skill.tools` items

**StepOutcome Self-Correction:**
- After each step, `StepSupervisorAgent` returns one of: SUCCESS, RETRY, REPLAN, ABORT
- RETRY: re-execute same step with supervisor feedback injected (max 1 retry per step)
- REPLAN: generate entirely new plan (max 3 replans per request)
- ABORT: stop execution, return error to user

**Streaming Architecture:**
- All execution paths use `AsyncGenerator` chains
- `AgentChunk` is the streaming unit (`shared/streaming.py`)
- `ChunkFilter` in adapters controls what reaches the user based on verbosity level

**Observability:**
- OpenTelemetry spans on all major operations (tracing via `core/observability/tracing.py`)
- Debug events written to `data/debug_logs.jsonl` (JSONL, not DB) via `core/observability/debug_logger.py`
- Structured error codes in `core/observability/error_codes.py`
- OTel metrics in-memory snapshot via `core/observability/metrics.py`
- SQLAlchemy instrumented for DB query tracing

**Self-Healing Skills:**
- `SkillQualityAnalyser` evaluates conversations post-completion
- Scores below threshold trigger `SkillImprovementProposal` generation
- Improvements written as context overlays; admins can revert or promote to global `/skills/`

## Entry Points

**HTTP API:**
- Location: `services/agent/src/interfaces/http/app.py`
- Triggers: HTTP requests from Open WebUI, CLI clients
- Responsibilities: FastAPI app creation, router registration, middleware setup

**Application Lifespan:**
- Location: `services/agent/src/interfaces/http/bootstrap.py`
- Triggers: FastAPI startup/shutdown
- Responsibilities: DB pool init, LiteLLM client, SkillRegistry load, tool registration, system context seeding

**Agent Endpoint (primary):**
- Location: `services/agent/src/interfaces/http/agent_api.py`
- Triggers: POST requests from Open WebUI pipeline
- Responsibilities: Validate request, call `AgentService.execute_stream()`, stream SSE events

**OpenWebUI Adapter:**
- Location: `services/agent/src/interfaces/http/openwebui_adapter.py`
- Triggers: POST `/v1/chat/completions` (OpenAI-compatible)
- Responsibilities: Context resolution, Dispatcher call, SSE formatting with `ChunkFilter`

**Scheduler Adapter:**
- Location: `services/agent/src/interfaces/scheduler/adapter.py`
- Triggers: Croniter-based in-process asyncio loop (60s check interval)
- Responsibilities: Execute `ScheduledJob` entries as AgentService requests

**Telegram Adapter:**
- Location: `services/agent/src/interfaces/telegram/adapter.py`
- Triggers: Telegram Bot API webhook/polling
- Responsibilities: Context resolution via `ContextService`, dispatch, plain-text rendering

## Error Handling

**Strategy:** Structured error codes + supervisor-driven self-correction

**Patterns:**
- `StepOutcome.RETRY`: transient errors (timeout, rate limit) trigger one automatic retry
- `StepOutcome.REPLAN`: output mismatch triggers full replanning (max 3)
- `StepOutcome.ABORT`: auth failures, invalid input, max retries exceeded
- `ToolConfirmationError` (`core/tools/base.py`): raised when tool needs HITL confirmation before proceeding
- Structured codes in `core/observability/error_codes.py` categories: TOOL_*, LLM_*, DB_*, NET_*, RAG_*

## Cross-Cutting Concerns

**Logging:** Python standard logging + OTel log bridge (WARNING+ bridged to OTel LoggerProvider when `OTEL_EXPORTER_OTLP_ENDPOINT` is set)

**Validation:** Pydantic models for all API boundaries (`shared/models.py`, `interfaces/http/schemas/`); `SkillRegistry` validates skill frontmatter at startup

**Authentication:**
- Open WebUI: Entra ID session forwarded via `X-OpenWebUI-User-*` headers; role is authoritative from DB after first login
- Internal API: `AGENT_INTERNAL_API_KEY` (Bearer or X-API-Key header)
- Diagnostic API: `AGENT_DIAGNOSTIC_API_KEY`
- Credentials: Fernet-encrypted, context-scoped in `user_credentials` table

**Security:**
- SSRF protection in `modules/fetcher/`
- CSP headers on all responses (`interfaces/http/middleware.py`)
- Architecture baseline validator (`.architecture-baseline.json`) enforces layer rules
- OAuth tokens and bearer credentials Fernet-encrypted at rest

---

*Architecture analysis: 2026-03-28*
