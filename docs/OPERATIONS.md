# Operations

Operational runbooks target the Python-first stack managed by the Typer CLI. Commands are idempotent and safe to repeat.

## Prerequisites
- Copy `.env.template` to `.env` and supply secrets or API keys.
- Install dependencies locally with `poetry install` when running tests outside Docker.
- Ensure Docker Desktop (or compatible engine) is running with GPU access for Ollama if required.

## Stack Lifecycle
```bash
# Start or update the full stack (builds images when needed)
python -m stack up

# Stop containers but keep volumes and models
python -m stack down

# View service status and health checks
python -m stack status

# Tail logs for a specific service
python -m stack logs agent
```

All commands read `.env`; rerunning `up` is safe because the CLI diff-checks the Compose configuration before recreating containers.

## Health Checks
```bash
# Agent service
curl -s http://localhost:8000/healthz | jq

# Qdrant
curl -s http://localhost:6333/collections | jq '.result | keys'

# Webfetch
curl -s http://localhost:8081/healthz | jq
```

> LiteLLM is still covered by Docker Compose healthchecks and `python -m stack status`.
> Manual probes are intentionally omitted to avoid GPU spin-up on developer machines.

## Smoke Tests
```bash
# Minimal agent completion
curl -sS -X POST http://localhost:8000/v1/agent \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "Summarise the stack services in one sentence.",
        "metadata": {"source": "operations-smoke"}
      }' | jq '.response'

# Open WebUI pathway (agent proxies to LiteLLM/Ollama)
curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "ollama/llama3",
        "messages": [
          {"role": "system", "content": "You are a helpful agent."},
          {"role": "user", "content": "Confirm that requests flow through the agent."}
        ]
      }' | jq '.choices[0].message.metadata'

# Tool-assisted research via webfetch
curl -sS -X POST http://localhost:8000/v1/agent \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "Use the web_fetch tool to gather two recent headlines about Qdrant.",
        "metadata": {
          "tools": ["web_fetch"],
          "tool_calls": [
            {"name": "web_fetch", "args": {"url": "https://qdrant.tech"}}
          ]
        }
      }' | jq '.response'
```

Expected behaviour: both endpoints return a `conversation_id`, echo the prompt
within `messages`, and expose metadata (including tool execution) in the
response payload. The `steps` array should list the orchestration trace in the
order memory → tools → completion.

## Maintenance
- **Model management**: use `ollama run qwen2.5:14b-instruct-q4_K_M` inside the `ollama` container to warm models.
- **Database**: the agent stores conversation metadata in `./data/agent_state.sqlite`. Back up or prune the file as part of maintenance.
- **LiteLLM configuration**: adjust routing or budgets via environment variables in `.env` or `docker-compose.yml`, then run `python -m stack up` to reload.
- **Qdrant backups**: snapshot volumes using `docker run --rm --volumes-from qdrant -v $(pwd)/backups:/backups alpine tar czf /backups/qdrant-$(date +%Y%m%d-%H%M%S).tgz /qdrant/storage`.
- **Dependency updates**: when `scripts/deps_check.py` flags new versions, validate
  changes by running the stack smoke tests in this guide and `poetry run pytest`.
  Merge only after both checks succeed.

### Qdrant memory ID migration

Older deployments used composite point IDs (`conversation_id:index`) for semantic memories. The
service now assigns opaque UUIDs to avoid collisions across batches while keeping the
`conversation_id` in the payload for filtering. To migrate existing data, run the helper script
once per environment:

```bash
poetry run python scripts/migrate_qdrant_memory_ids.py \
  --url "http://localhost:6333" \
  --collection "agent-memories"
```

The script scrolls through the configured collection, re-inserts each point with a new UUID, and
deletes the original identifiers so historical conversations remain discoverable.

## Troubleshooting
- **Service unhealthy**: `python -m stack logs <service>` to inspect container output.
- **Port conflicts**: review overrides in `.env`; adjust `AGENT_PORT`, `LITELLM_PORT`, etc., then rerun `python -m stack up`.
- **Dependency mismatch**: run `poetry lock --check` to ensure the lock file is current before rebuilding images.

## Operational Checklist Before Release
1. Stack commands succeed twice consecutively (`up` ➜ `down` ➜ `up`).
2. Smoke tests above return HTTP 200 with valid JSON.
3. `poetry run ruff check .` and `poetry run pytest -v` succeed locally or in CI.
4. Documentation updates merged into `docs/architecture/` and this runbook.
