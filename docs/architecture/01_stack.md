# Runtime Stack

## Compose Services

[`docker-compose.yml`](../../docker-compose.yml) in the repository root
defines the complete stack. Compose automatically reads `.env`, so set the
variables you need there (or export them in your shell) before running
`python -m stack up`.

| Service     | Purpose | Ports | Healthcheck |
|-------------|---------|-------|-------------|
| `openwebui` | Web UI frontend that proxies chat through the agent | `3000 → 8080` | `curl -f http://localhost:8080` |
| `agent`     | FastAPI agent orchestrator and tool runner | `8000` | `curl -f http://localhost:8000/healthz` |
| `litellm`   | Gateway to OpenRouter LLMs | `4000` | `curl -f http://localhost:4000/health` |
| `qdrant`    | Vector memory for semantic recall | `6333` | `wget -qO- http://localhost:6333/healthz` |
| `searxng`   | Metasearch backend for web search | `8080` | `wget -qO- http://localhost:8080/` |
| `postgres`  | Database for conversation and context storage | `5432` | `pg_isready -U postgres` |

Volumes:

- `qdrant-data` keeps vector data between restarts.
- `postgres_data` persists database state.

## Stack CLI

The Stack CLI wraps Docker Compose commands and surfaces status information.

```
python -m stack up        # Start or restart the full stack
python -m stack status    # Render a Rich table of container health
python -m stack logs openwebui --tail 100  # defaults to the last 100 lines; add --follow interactively
python -m stack down      # Stop containers (idempotent)
```

`--follow` streams until interrupted; avoid using it in agent automation because it never exits on its own.

The CLI loads environment variables from `.env` and merges them with the shell
environment. All commands are idempotent: re-running `up` simply ensures the
stack is running, and `down` succeeds even when the containers are already
stopped.

Copy `.env.template` to `.env` before running the CLI. Additional overrides can
live in `.env.local` or direct environment exports. Compose automatically reads
`.env`; the stack CLI also injects it when running Docker commands.

Open WebUI is wired to the agent by default via the `OPENAI_API_BASE_URL`
variable in `docker-compose.yml`. All chat requests are sent to the agent's
`/v1/chat/completions` endpoint, which then relays to LiteLLM and OpenRouter.

## Health Checks

- Each container defines a Compose healthcheck, enabling dependency ordering
  and reliable readiness detection.
- `python -m stack status` reports the Docker health state for all containers.
- `/healthz` endpoint on the agent service is used by Compose and external
  monitoring.
