---
focus: tech
generated: 2026-03-28
---

# Technology Stack

**Analysis Date:** 2026-03-28

## Languages

**Primary:**
- Python 3.11–3.12 (pinned range `>=3.11,<3.13`; Docker image uses `python:3.11-slim`; runtime is 3.12 per project memory)

**Secondary:**
- HTML/CSS/JS - Admin portal templates in `services/agent/src/interfaces/http/templates/`
- YAML - Skill definitions in `skills/`, tool config in `services/agent/config/tools.yaml`, model registry in `services/agent/config/models.yaml`

## Runtime

**Environment:**
- Docker Compose (multi-service stack)
- Python runtime: 3.12 in production (`python:3.11-slim` base image, upgraded at install)
- Node.js 22 (optional build arg `INCLUDE_VAULT=true`) for `obsidian-headless` CLI
- Node.js 20 (optional build arg `INCLUDE_NODEJS=true`) for `@google/gemini-cli`

**Package Manager:**
- Poetry 1.8.2
- Lockfile: `services/agent/poetry.lock` (present)
- Build system: `poetry-core>=1.8.0`

## Frameworks

**Core:**
- FastAPI `^0.133.0` - HTTP server, admin portal, OpenAI-compatible `/v1` API
- Uvicorn `0.38.0` (with `standard` extras) - ASGI server
- Pydantic `^2.12.0` - Data validation, settings management
- SQLAlchemy `^2.0.45` - Async ORM (2.0 style)
- Alembic `^1.17.2` - Database migrations
- asyncpg `^0.31.0` - Async PostgreSQL driver

**AI / LLM:**
- LiteLLM `^1.81.0` - LLM gateway client (calls internal LiteLLM proxy)
- MCP `^1.26.0` - Model Context Protocol client (user-defined MCP server connections)
- langchain-text-splitters `^1.1.0` - Document chunking for RAG
- trafilatura `^2.0.0` - Web page content extraction
- numpy `^1.26.4` - Vector operations

**Observability:**
- opentelemetry-sdk `^1.39.0` - Tracing and metrics
- opentelemetry-exporter-otlp `^1.39.0` - OTLP span/log export (optional, env-gated)
- openinference-instrumentation-litellm `^0.1.29` - Auto-instrument LiteLLM calls
- opentelemetry-instrumentation-fastapi `^0.60b1` - Auto-instrument HTTP requests
- opentelemetry-instrumentation-sqlalchemy `^0.60b1` - Auto-instrument DB queries
- python-json-logger `^2.0.7` - Structured JSON logging

**Messaging:**
- aiogram `^3.10.0` - Telegram Bot API (async)

**Auth / Security:**
- cryptography `^46.0.3` - Fernet symmetric encryption for stored credentials and OAuth tokens
- pyjwt `^2.10.1` - JWT signing for admin portal sessions
- slowapi `^0.1.9` - Rate limiting middleware (wraps limits)

**Utilities:**
- httpx `0.28.1` - Async HTTP client
- python-dotenv `^1.0.1` - `.env` loading
- pyyaml `^6.0.1` - YAML parsing (tools.yaml, models.yaml, skills)
- croniter `^3.0` - Cron expression parsing for scheduler
- orjson `^3.10` - Fast JSON serialization
- pathspec `^0.12.1` - Gitignore-style path matching
- python-multipart `^0.0.22` - Form data parsing (admin portal uploads)
- rich `14.2.0` - Terminal output formatting (stack CLI)
- typer `0.20.0` - CLI framework (stack CLI)
- docker `^7.1.0` - Docker SDK (stack CLI deploys)
- azure-devops `^7.1.0b4` - Azure DevOps REST API client

**Testing:**
- pytest `9.0.0`
- pytest-asyncio `1.3.0`
- pytest-cov `^6.0` - Coverage
- pytest-testmon `^2.2.0` - Selective test execution (CI: skips unaffected tests)
- pytest-xdist `^3.8.0` - Parallel test execution
- aiosqlite `^0.22.1` - In-memory SQLite for tests
- coverage `7.11.3`

**Code Quality:**
- ruff `0.14.4` - Linting (rules: E, F, I, B, UP, S, N; complexity < 18)
- black `25.11.0` - Formatting (line length: 100)
- mypy `^1.10.0` - Static type checking (strict mode, disallows `Any`)

## Infrastructure Services (Docker Compose)

**LiteLLM Proxy** - `ghcr.io/berriai/litellm:v1.63.8`
- Internal port: 4000
- Routes to OpenRouter for all LLM calls
- Config: `services/litellm/config.yaml`
- Budget limit: $5.00 (configurable)

**PostgreSQL** - `postgres:15-alpine`
- Internal port: 5432
- Primary relational database (contexts, sessions, conversations, credentials, etc.)
- Connection URL: `postgresql+asyncpg://postgres:<password>@postgres:5432/agent_db`

**Qdrant** - `qdrant/qdrant:v1.17.0`
- Internal port: 6333
- Vector database for semantic memory and RAG
- Collection: `agent-memories`
- Storage: bind-mounted `./data/qdrant`

**SearXNG** - `searxng/searxng@sha256:d477c0...` (pinned SHA digest)
- Internal port: 8080
- Self-hosted meta search engine (web search for agent tools)

**Open WebUI** - `ghcr.io/open-webui/open-webui:0.8.3`
- Chat interface for end users
- Connects to agent's OpenAI-compatible `/v1` endpoint
- Microsoft Entra ID OIDC authentication

**Traefik** - `traefik:v3.0` (production only, `docker-compose.prod.yml`)
- Reverse proxy with automatic Let's Encrypt TLS
- Strips `X-OpenWebUI-*` headers on external ingress (auth bypass protection)
- Exposes ports 80/443 only; all internal services unexposed

## Build and Packaging

**Stack CLI** - `./stack` (Typer-based, defined in `services/agent/src/stack/cli.py`)
- Entry point: `stack_cli_wrapper.py` at project root
- Commands: `check`, `lint`, `typecheck`, `test`, `dev up/down/deploy/restart/logs`, `deploy`, `up/down/restart/logs/status/health`
- Subprocess timeout: 900s for quality checks (920+ tests)

**Docker:**
- `DOCKER_BUILDKIT=1` enabled
- Single `Dockerfile` at `services/agent/Dockerfile`
- Base: `python:3.11-slim`
- Optional Node.js layers controlled by build args `INCLUDE_VAULT` and `INCLUDE_NODEJS`
- Image tag: `ai-agent-platform-agent:latest`

**Environments:**
- `docker-compose.yml` - Base services (all)
- `docker-compose.override.yml` - Local dev port exposure (auto-loaded by Docker Compose)
- `docker-compose.dev.yml` - Dev stack with Traefik routing, separate DB volumes
- `docker-compose.prod.yml` - Production with Traefik, restart policies, no exposed ports

## LLM Providers and Models

All LLM calls route through the internal LiteLLM proxy (`http://litellm:4000`), which forwards to **OpenRouter** (`https://openrouter.ai/api/v1`).

**Model Aliases** (defined in `services/agent/config/models.yaml` and `services/litellm/config.yaml`):

| Alias | Resolved Model | Use |
|-------|---------------|-----|
| `planner` | `openai/gpt-oss-120b:exacto` | Plan generation |
| `supervisor` | `openai/gpt-oss-120b:exacto` | Step outcome evaluation |
| `composer` | `openai/gpt-oss-120b:exacto` | Final answer composition |
| `skillsrunner` | `openai/gpt-oss-120b:exacto` | Default skill execution |
| `skillsrunner_deep` | `google/gemini-2.5-flash` | Large-context skills (1M ctx) |
| `software_engineer` | `google/gemini-2.5-flash` | Code investigation/fix |
| `price_tracker` | `meta-llama/llama-4-scout` | Price extraction (fast) |
| `price_tracker_fallback` | `anthropic/claude-haiku-4.5` | Price extraction fallback |
| `agentchat` | `openai/gpt-oss-120b:exacto` | General chat skills |
| `embedder` | `qwen/qwen3-embedding-8b` | Text embeddings (multilingual, 4096-dim) |

**ZDR Routing** (Zero Data Retention via OpenRouter `extra_body.provider`):
- Primary models routed: `Groq > DeepInfra > Novita`
- Gemini models routed via: `Google Vertex`

**Reasoning-capable models** with separate `reasoning_content` field:
- `openai/gpt-oss-120b:exacto` (Harmony format)
- `deepseek/deepseek-r1-0528`, `deepseek/deepseek-v3.1-terminus`
- `qwen/qwen3-235b-a22b-thinking-2507`, `qwen/qwen3-next-80b-a3b-thinking`, `qwen/qwen3-vl-235b-a22b-thinking`
- `google/gemini-2.5-pro-preview`, `google/gemini-3-pro-preview`
- `minimax/minimax-m1`, `minimax/minimax-m2`
- `z-ai/glm-4.5`, `z-ai/glm-4.6:exacto`

**Anthropic Claude models** (separate `thinking` field):
- `anthropic/claude-sonnet-4`, `anthropic/claude-opus-4.1`, `anthropic/claude-3.7-sonnet`

## Configuration

**Environment:**
- Loaded from `.env` via `python-dotenv` on service startup
- Settings class: `services/agent/src/core/runtime/config.py` (`Settings(BaseModel)`)
- Env prefix: `AGENT_` for most settings
- Production validation: requires `AGENT_CREDENTIAL_ENCRYPTION_KEY`, `AGENT_ADMIN_JWT_SECRET`, `AGENT_INTERNAL_API_KEY`

**Build:**
- `services/agent/pyproject.toml` - Python dependencies and tool config
- `services/agent/config/tools.yaml` - Tool registry (enabled tools and args)
- `services/agent/config/models.yaml` - Model capability registry (reasoning mode per model)
- `services/litellm/config.yaml` - LiteLLM proxy model list, routing, budget

## Platform Requirements

**Development:**
- Docker Compose v2
- Poetry 1.8.2
- Python 3.11–3.12
- `.env` file populated from `.env.template`

**Production:**
- Linux host with Docker (tested on Ubuntu/Tuxedo)
- Traefik for TLS termination and routing
- PostgreSQL data persisted in Docker named volume `postgres_data`
- Qdrant data persisted via bind mount `./data/qdrant`
- OTel span logs persisted via bind mount `./services/agent/data`

---

*Stack analysis: 2026-03-28*
