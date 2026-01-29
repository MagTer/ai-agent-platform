# Operations

Operational runbooks target the Python-first stack managed by the Typer CLI. The
canonical configuration lives in the root `docker-compose.yml`. Commands are
idempotent and safe to repeat.

## Prerequisites
- Copy `.env.template` to `.env` and supply secrets or API keys (notably `OPENROUTER_API_KEY`).
- Install dependencies locally with `poetry install` when running tests outside Docker.
- Ensure Docker Desktop (or compatible engine) is running.
- Review the [Contributing Guide](./contributing.md) so operational changes capture the
  Codex checklist updates (tests, docs, dependency notes).

## Stack Lifecycle
```bash
# Start or update the full stack (builds images when needed)
poetry run stack up
# Alternative: python scripts/stack_tools.py up --check-litellm

# Stop containers but keep volumes
poetry run stack down
# Alternative: python scripts/stack_tools.py down --remove-volumes

# View service status and health checks
poetry run stack status
# Alternative: python scripts/stack_tools.py health

# Tail logs for a specific service (defaults to the last 100 lines)
poetry run stack logs agent --tail 100
# Example: stream logs interactively with --follow (do not use follow in automation because it never exits)
# Alternative: python scripts/stack_tools.py logs agent
```

All commands read `.env`; rerunning `up` is safe because the CLI diff-checks the Compose configuration before recreating containers.

## Automation Catalogue

Python scripts under `scripts/` supersede the original PowerShell helpers. They
wrap Docker commands with the same guardrails (environment discovery, health
waits, idempotent behaviour) while working across macOS, Linux, and Windows.

| Task | Command | Notes |
|------|---------|-------|
| Bring the stack up (waits for health) | `poetry run stack up` | Supports `--check-litellm`, `--build`, `--bind-mounts`. |
| Stop the stack | `poetry run stack down` | Add `--remove-volumes` to delete persistent data. |
| Probe service health | `poetry run stack health [service]` | Mirrors `Stack-Health.ps1`. |
| Tail logs | `poetry run stack logs [service …] --tail <lines>` | Defaults to 100 lines; add `--follow` only when monitoring interactively. |
| Snapshot the repository | `poetry run stack repo save` | Validates Compose config, avoids committing on `main`. |
| Ensure Qdrant schema | `poetry run stack qdrant ensure-schema` | Creates or recreates the configured collection. |
| Backup/restore Qdrant | `poetry run stack qdrant backup` / `restore` | Archives `/qdrant/storage` and restarts the service safely. |

## Health Checks
```bash
# Agent service
curl -s http://localhost:8000/healthz | jq

# Qdrant
curl -s http://localhost:6333/collections | jq '.result | keys'
```

> LiteLLM is covered by Docker Compose healthchecks and `python -m stack status`.

## Observability (Arize Phoenix)

The platform includes **Arize Phoenix** for LLM tracing and observability.

- **Dashboard:** [http://localhost:6006](http://localhost:6006)
- **Features:**
    - View traces for all agent interactions.
    - Inspect inputs/outputs for every LLM call (prompts & completions).
    - Visualize tool execution and latency.
    - Debug errors in the retrieval or planning steps.

The agent sends OpenTelemetry (OTLP) traces to Phoenix automatically.

## Smoke Tests
```bash
# Minimal agent completion
curl -sS -X POST http://localhost:8000/v1/agent \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "Summarise the stack services in one sentence.",
        "metadata": {"source": "operations-smoke"}
      }' | jq '.response'

# Open WebUI pathway (agent proxies to LiteLLM/OpenRouter)
curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
                  "model": "planner",
        "messages": [
          {"role": "system", "content": "You are a helpful agent."},
          {"role": "user", "content": "Confirm that requests flow through the agent."}
        ]
      }' | jq '.choices[0].message'

# Tool-assisted research via web search
curl -sS -X POST http://localhost:8000/v1/agent \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "Use web search to find two recent headlines about Qdrant.",
        "metadata": {"tools": ["web_search"]}
      }' | jq '.response'
```

Expected behaviour: both endpoints return a `conversation_id`, echo the prompt
within `messages`, and expose metadata (including tool execution) in the
response payload.

## Maintenance
- **Database**: conversation metadata is stored in Postgres. Back up via `pg_dump` as part of maintenance.
- **LiteLLM configuration**: adjust routing or budgets via environment variables in `.env` or edit `services/litellm/config.yaml`, then run `poetry run stack up` to reload.
- **Qdrant backups**: `poetry run stack qdrant backup --backup-dir backups` creates timestamped archives.
- **Repository snapshots**: run `poetry run stack repo save --message "chore: ops snapshot"` to validate Compose and commit changes.
- **Dependency updates**: when `scripts/deps_check.py` flags new versions, validate via smoke tests and `poetry run pytest`.

## Troubleshooting
- **Service unhealthy**: `python -m stack logs <service>` to inspect container output.
- **Port conflicts**: review overrides in `.env`; adjust `AGENT_PORT`, `LITELLM_PORT`, etc., then rerun `python -m stack up`.
- **Dependency mismatch**: run `poetry lock --check` to ensure the lock file is current before rebuilding images.

## Operational Checklist Before Release
1. Stack commands succeed twice consecutively (`up` → `down` → `up`).
2. Smoke tests above return HTTP 200 with valid JSON.
3. `poetry run ruff check .` and `poetry run pytest -v` succeed locally or in CI.
4. Documentation updates merged into `docs/architecture/` and this runbook.
5. Codex PR template checklist completed.
