# Dev/Prod Environment Architecture

This document describes the definitive architecture for running the platform
in development and production environments side by side on the same host.

## Overview

The platform uses Docker Compose with environment-specific overlay files:

```
docker-compose.yml          # Base services (shared config)
docker-compose.dev.yml      # Dev overrides (separate volumes, dev domain)
docker-compose.prod.yml     # Prod overrides (Traefik, SSL, restart policies)
```

Each environment runs as a separate Docker Compose project:

| Aspect | Development | Production |
|--------|-------------|------------|
| Project name | `ai-agent-platform-dev` | `ai-agent-platform-prod` |
| CLI command | `./stack dev deploy` | `./stack deploy` |
| Compose files | base + dev.yml | base + prod.yml |
| Domain | `agent-dev.example.com` | `agent.example.com` |

## Data Isolation

Dev and prod have completely separate data:

| Data | Dev | Prod |
|------|-----|------|
| PostgreSQL | Volume: `ai-agent-platform-dev_postgres_data_dev` | Volume: `ai-agent-platform-prod_postgres_data` |
| PostgreSQL DB name | `agent_db_dev` | `agent_db` |
| Qdrant | Bind mount: `data/qdrant_dev/` | Bind mount: `data/qdrant/` |
| Open WebUI | Bind mount: `data/openwebui_dev/` | Bind mount: `data/openwebui/` |
| Let's Encrypt | N/A (uses prod Traefik) | Bind mount: `data/letsencrypt/` |

## Network Architecture

Both environments use internal Docker networks with no direct host port exposure:

- **Prod:** Traefik runs as part of the prod stack, listens on ports 80/443
- **Dev:** The prod Traefik connects to the dev network for routing

## Project Name Safety

**CRITICAL:** The Docker Compose project name determines volume names. A project
name change creates new empty volumes, causing data loss.

The project names are hardcoded in `services/agent/src/stack/utils.py`:

```python
DEV_PROJECT_NAME = "ai-agent-platform-dev"
PROD_PROJECT_NAME = "ai-agent-platform-prod"
```

**DO NOT:**
- Set `COMPOSE_PROJECT_NAME` in `.env` (Docker reads this variable directly)
- Set `STACK_PROJECT_NAME` in `.env` (the stack CLI reads this)
- Run `docker compose up` without the `-p` flag (it uses COMPOSE_PROJECT_NAME or directory name)

**Always use the stack CLI** (`./stack dev deploy`, `./stack deploy`) which passes
the correct `-p` flag automatically.

## Backup System

Backups run automatically before every deploy:

```bash
# Manual backup
./stack backup              # Backup prod (default)
./stack backup --dev        # Backup dev

# List backups
./stack backup list

# Restore
./stack backup restore data/backups/prod_20260223_172400.sql.gz
./stack backup restore data/backups/dev_20260223_172400.sql.gz --dev
```

Backups are stored in `data/backups/` (gitignored). Default retention: 5 per environment.

## Volume Existence Check

The deploy commands (`./stack deploy`, `./stack dev deploy`) check that the
expected postgres volume exists before proceeding. If the volume is missing,
a loud warning is printed:

```
WARNING: Docker volume 'ai-agent-platform-dev_postgres_data_dev' does not exist!
A new empty volume will be created. This may cause DATA LOSS.
```

## What Should Be Different (By Design)

| Aspect | Dev | Prod | Reason |
|--------|-----|------|--------|
| Project name | `-dev` suffix | `-prod` suffix | Volume isolation |
| Database name | `agent_db_dev` | `agent_db` | Data isolation |
| Domain | `DOMAIN_DEV` | `DOMAIN` | Separate URLs |
| AGENT_ENVIRONMENT | `development` | `production` | Feature flags |
| Restart policy | none | `unless-stopped` | Dev can crash |
| Resource reservations | none | configured | Prod stability |
| Traefik | Connects from prod | Runs in stack | Single proxy |

## What Must Be The Same (Structural Parity)

| Aspect | Notes |
|--------|-------|
| Services | Same set of services in both |
| Port exposure | No direct host ports (Traefik only) |
| Health checks | Same check endpoints |
| Security headers | Same header stripping |
| Logging | Same json-file driver config |

## Environment Variables

The `.env` file contains shared secrets and configuration. Environment-specific
overrides are in the compose overlay files, not in `.env`.

**Intentionally absent from .env:**
- `STACK_PROJECT_NAME` -- hardcoded in utils.py
- `COMPOSE_PROJECT_NAME` -- not needed (stack CLI always passes -p)
