---
focus: tech
generated: 2026-03-28
---

# External Integrations

**Analysis Date:** 2026-03-28

## APIs & External Services

**LLM Gateway:**
- OpenRouter - All LLM completions and embeddings
  - SDK/Client: LiteLLM proxy (`ghcr.io/berriai/litellm:v1.63.8`) as internal gateway; agent calls `http://litellm:4000`
  - Auth env var: `OPENROUTER_API_KEY`
  - ZDR routing configured per-model in `services/litellm/config.yaml`
  - Budget limit: $5.00 (configurable in `services/litellm/config.yaml`)

**Azure DevOps:**
- Work item management (create, update, query backlog items)
  - SDK/Client: `azure-devops ^7.1.0b4`
  - Tool: `services/agent/src/core/tools/azure_devops.py`
  - Auth: Personal Access Token (PAT), stored per-context as encrypted credential
  - Credential type key: `azure_devops_pat`
  - Global defaults (optional): `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_PAT`
  - Context credentials take precedence over env vars

**Homey Smart Home:**
- Control lights, sensors, and automation flows via Homey Web API
  - SDK/Client: `httpx` (REST calls to `https://api.athom.com`)
  - Tool: `services/agent/src/core/tools/homey.py`
  - Auth: OAuth 2.0 Authorization Code Grant with PKCE
  - Tokens stored encrypted in database per context
  - Client registration: `https://tools.developer.homey.app/`
  - Auth env vars: `AGENT_HOMEY_CLIENT_ID`, `AGENT_HOMEY_CLIENT_SECRET`, `AGENT_HOMEY_AUTHORIZATION_URL`, `AGENT_HOMEY_TOKEN_URL`, `AGENT_OAUTH_REDIRECT_URI`
  - Feature flag: `AGENT_HOMEY_OAUTH_ENABLED` (default: `true`)

**GitHub:**
- Pull request creation for development workflow
  - SDK/Client: `gh` CLI (subprocess call, no Python SDK)
  - Tool: `services/agent/src/core/tools/github_pr.py`
  - Auth: `GITHUB_TOKEN` env var (PAT with repo permissions)
  - Used in conjunction with `git_clone` and `claude_code` tools

**Obsidian Vault (obsidian-headless):**
- Per-context read/write access to user's Obsidian Sync vault
  - SDK/Client: `ob` CLI from `obsidian-headless` npm package (subprocess; Node.js 22 required)
  - Tool: `services/agent/src/core/tools/vault.py`
  - Auth: Per-context auth token stored via `CredentialService` (credential type: `obsidian_vault`)
  - Vault path: `/vault/<context_id>/` (Docker volume `vault_data`)
  - Requires `INCLUDE_VAULT=true` Docker build arg

**Resend (Email):**
- Transactional email delivery (send reports, summaries, reminders to users)
  - SDK/Client: `httpx` REST calls to Resend API
  - Tool: `services/agent/src/core/tools/send_email.py`
  - Service: `services/agent/src/modules/email/service.py` (`ResendEmailService`)
  - Auth env var: `AGENT_RESEND_API_KEY`
  - From address: `AGENT_EMAIL_FROM_ADDRESS`
  - Only sends to authenticated user (no arbitrary recipients)

**SearXNG (Web Search):**
- Self-hosted meta search engine
  - SDK/Client: `httpx` REST calls
  - Tool: `services/agent/src/core/tools/web_search.py`; underlying fetcher: `services/agent/src/modules/fetcher/__init__.py`
  - Connection env var: `SEARXNG_URL` (default: `http://searxng:8080`)
  - Auth: `SEARXNG_SECRET` (internal HMAC secret, not exposed externally)
  - Runs as internal Docker service, not publicly accessible

**Claude Code CLI:**
- Delegate code investigation/fix tasks to Claude Code subprocess
  - SDK/Client: Subprocess call to `claude` CLI binary
  - Tool: `services/agent/src/core/tools/claude_code.py`
  - Auth: Inherits ambient Claude credentials from container environment
  - Operates in context-isolated workspace: `/tmp/agent-workspaces/<context_id>/`

## Data Storage

**Databases:**
- PostgreSQL 15 (Alpine)
  - Connection env var: `POSTGRES_URL` (e.g., `postgresql+asyncpg://postgres:<pw>@postgres:5432/agent_db`)
  - Credentials: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
  - Client: SQLAlchemy 2.0 async (`asyncpg` driver)
  - Migrations: Alembic (`services/agent/alembic/`)
  - Models: `services/agent/src/core/db/models.py` (Context, Session, Conversation, Message, Workspace, OAuthToken, etc.)
  - Dev database name: `agent_db_dev` (separate volume `postgres_data_dev`)

**Vector Store:**
- Qdrant v1.17.0
  - Connection env var: `AGENT_QDRANT_URL` (default: `http://qdrant:6333`), also `QDRANT_URL`
  - API key env var: `AGENT_QDRANT_API_KEY` (optional, for remote Qdrant)
  - Collection: `AGENT_QDRANT_COLLECTION` / `QDRANT_COLLECTION` (default: `agent-memories`)
  - Client: `qdrant-client ^1.16.0`
  - Used for semantic memory and RAG

**File Storage:**
- Local bind mounts (no cloud object storage)
  - Span/trace logs: `services/agent/data/` → `/app/data` in container
  - Qdrant data: `./data/qdrant` (prod), `./data/qdrant_dev` (dev)
  - Open WebUI data: `./data/openwebui` (prod), `./data/openwebui_dev` (dev)
  - Obsidian vault: Docker named volume `vault_data` → `/vault`
  - Let's Encrypt certs: `./data/letsencrypt`

**Caching:**
- In-memory only (no Redis)
  - Qdrant response caching: file-based cache in `WebFetcher` at `~/.cache/agent-fetcher`
  - MCP client pool: in-process pool (`services/agent/src/core/mcp/client_pool.py`)
  - Homey device list: in-database cache with 36-hour TTL

## Authentication & Identity

**Microsoft Entra ID (Azure AD):**
- Primary user authentication for Open WebUI and Admin Portal
  - Provider: Microsoft OIDC (`https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration`)
  - Open WebUI: native Entra ID OAuth integration via env vars `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_CLIENT_TENANT_ID`
  - Admin Portal: custom OAuth 2.0 flow in `services/agent/src/interfaces/http/admin_auth_oauth.py`
  - Admin session: JWT signed with `AGENT_ADMIN_JWT_SECRET`
  - Role claim: `roles` claim, admin role configurable via `AGENT_ENTRA_ADMIN_ROLES` (default: `platform-admin`)
  - App registration env vars: `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_CLIENT_TENANT_ID`

**Internal API Key Auth:**
- Agent endpoints (`/v1/...`): `AGENT_INTERNAL_API_KEY` via `Authorization: Bearer` or `X-API-Key` header
- Admin portal: `AGENT_ADMIN_API_KEY`
- Diagnostic API (`/platformadmin/api/`): `AGENT_DIAGNOSTIC_API_KEY` via `X-Api-Key` header (case-sensitive alias)
- All keys: `openssl rand -hex 32`; required in production mode

**Credential Encryption:**
- All stored OAuth tokens and API credentials encrypted with Fernet symmetric encryption
- Key: `AGENT_CREDENTIAL_ENCRYPTION_KEY` (Fernet key, required in production)
- Implementation: `services/agent/src/core/auth/credential_service.py`
- Plaintext fallback in decrypt for pre-encryption tokens (backward compatibility)
- Credentials are context-scoped (not user-scoped)

**Telegram:**
- Bot-based user interface adapter
  - Library: `aiogram ^3.10.0`
  - Adapter: `services/agent/src/interfaces/telegram/adapter.py`
  - Auth env var: `AGENT_TELEGRAM_BOT_TOKEN`
  - Adapter disabled if token not provided

## MCP Servers (User-Defined)

- Users can connect arbitrary MCP servers per context via Admin Portal (`/platformadmin/mcp/`)
- Transport: auto-detect, SSE, or Streamable HTTP
- Auth options: None, Bearer token (Fernet-encrypted at rest), OAuth 2.0/2.1 with PKCE
- Connection data stored in database (`McpServer` model)
- MCP client pool: `services/agent/src/core/mcp/client_pool.py`
- Tool loader: `services/agent/src/core/tools/mcp_loader.py` (wraps MCP tools as `McpToolWrapper`)
- Tool names prefixed with server name to avoid collisions

## Monitoring & Observability

**OpenTelemetry:**
- Traces: `services/agent/src/core/observability/tracing.py`
  - In-process span capture with size-based rotation (`AGENT_TRACE_SPAN_LOG_PATH`, max 10MB, 3 files)
  - Optional OTLP export via `OTEL_EXPORTER_OTLP_ENDPOINT` env var (external APM, e.g., Jaeger, Honeycomb)
  - Auto-instruments FastAPI and SQLAlchemy
- Metrics: `services/agent/src/core/observability/metrics.py`
  - In-memory snapshot exposed at `/platformadmin/api/otel-metrics`
- Debug logs: JSONL at `data/debug_logs.jsonl` (not database)
  - Written by `services/agent/src/core/observability/debug_logger.py`
  - Queryable via Diagnostic API (`/platformadmin/api/debug/logs`)
- OTLP log bridge: Python `WARNING+` logs bridged to OTel `LoggerProvider` when `OTEL_EXPORTER_OTLP_ENDPOINT` is set

**Error Tracking:**
- No third-party error tracking (e.g., Sentry) configured
- Errors surfaced via OTel spans and structured JSON logs

**Logs:**
- Structured JSON via `python-json-logger`
- Log level: `AGENT_LOG_LEVEL` (default: `INFO`)
- Docker logging: `json-file` driver, 10MB max per file, 5 rotations

## CI/CD & Deployment

**Hosting:**
- Self-hosted Linux server (Docker Compose)
- Production domain: configured via `DOMAIN` env var
- Dev domain: `DOMAIN_DEV` (parallel stack)

**CI Pipeline:**
- GitHub Actions (workflows in `.github/workflows/`)
- Runs on PR only + weekly Monday cron (to seed testmon cache on main)
- Pipeline: Ruff → Black → Mypy → Pytest (with testmon selective execution)
- Quality gate command: `./stack check` (identical to CI)

**Deployment:**
- Production: `./stack deploy` (verifies main branch, runs checks, rebuilds agent container, zero-downtime restart)
- Dev: `./stack dev deploy` (build + health verification)
- No Kubernetes; pure Docker Compose

## Environment Configuration

**Required env vars (production must-have):**
- `POSTGRES_PASSWORD` - PostgreSQL password
- `AGENT_CREDENTIAL_ENCRYPTION_KEY` - Fernet key for credential encryption
- `AGENT_INTERNAL_API_KEY` - Protects agent API endpoints
- `AGENT_ADMIN_API_KEY` - Protects admin portal
- `AGENT_DIAGNOSTIC_API_KEY` - Protects diagnostic API
- `AGENT_ADMIN_JWT_SECRET` - Signs admin session JWTs
- `SEARXNG_SECRET` - SearXNG HMAC secret
- `OPENWEBUI_SECRET` - Open WebUI session secret
- `OPENROUTER_API_KEY` - All LLM completions

**Optional integrations:**
- `AZURE_DEVOPS_ORG`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_PAT` - Azure DevOps defaults
- `AGENT_HOMEY_CLIENT_ID`, `AGENT_HOMEY_CLIENT_SECRET` - Homey OAuth
- `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_CLIENT_TENANT_ID` - Entra ID
- `AGENT_RESEND_API_KEY` - Email sending
- `AGENT_TELEGRAM_BOT_TOKEN` - Telegram bot
- `GITHUB_TOKEN` - GitHub PR creation
- `OTEL_EXPORTER_OTLP_ENDPOINT` - External OTel APM export

**Secrets location:**
- `.env` file at project root (gitignored; copy from `.env.template`)
- Per-context credentials stored Fernet-encrypted in PostgreSQL (`credentials` table)
- OAuth tokens stored Fernet-encrypted in PostgreSQL (`oauth_tokens` table)

## Webhooks & Callbacks

**Incoming:**
- OAuth callback: `GET /auth/oauth/callback` - receives OAuth authorization codes (Homey, Entra ID)
- MCP OAuth callback: `GET /auth/oauth/callback` - MCP server OAuth flows
- Telegram webhook: not used; adapter uses long-polling via `aiogram`

**Outgoing:**
- No configured outgoing webhooks; all external calls are request-response

---

*Integration audit: 2026-03-28*
