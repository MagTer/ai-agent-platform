# Runtime Stack

## Compose Services

| Service    | Purpose | Ports | Healthcheck |
|------------|---------|-------|-------------|
| `openwebui`| Open WebUI frontend for reasoning (proxying through the agent) | 3000 → 8080 | `wget -qO- http://localhost:8080/` |
| `agent`    | FastAPI agent orchestrator | 8000 → 8000 | `curl -f http://localhost:8000/healthz` |
| `litellm`  | Gateway to local/remote LLMs | 4000 → 4000 | `wget -qO- http://localhost:4000/health` |
| `ollama`   | GPU accelerated inference engine | 11434 → 11434 | `ollama --version` |
| `qdrant`   | Vector memory for semantic recall | 6333 → 6333 | `wget -qO- http://localhost:6333/healthz` |
| `embedder` | Sentence-transformer embedding service | 8082 → 8082 | `wget -qO- http://localhost:8082/health` |
| `ragproxy` | Aggregates embedder + Qdrant for RAG responses | internal only | `wget -qO- http://localhost:4080/health` |
| `searxng`  | Metasearch engine backing the fetcher | 8080 → 8080 | `wget -qO- http://localhost:8080/` |
| `webfetch` | Browserless content retriever | 8081 → 8081 | `wget -qO- http://localhost:8081/health` |
| `n8n`      | Optional workflow engine | 5678 → 5678 | `wget -qO- http://localhost:5678/healthz` |

Volumes:

- `ollama-models` stores downloaded Ollama models.
- `qdrant-data` keeps vector data between restarts.
- `embedder-cache` stores sentence-transformer weights.
- `n8n-data` persists workflow state and credentials.

## Stack CLI

The Stack CLI wraps Docker Compose commands and surfaces status information.

```
python -m stack up        # Start or restart the full stack
python -m stack status    # Render a Rich table of container health
python -m stack logs openwebui --tail 100
python -m stack down      # Stop containers (idempotent)
```

The CLI loads environment variables from `.env` and merges them with the shell
environment. All commands are idempotent: re-running `up` simply ensures the
stack is running, and `down` succeeds even when the containers are already
stopped.

Copy `.env.template` to `.env` before running the CLI. Additional overrides can
live in `.env.local` or direct environment exports.

Open WebUI is wired to the agent by default via the `OPENAI_API_BASE_URL`
variable in [`docker-compose.yml`](../../docker-compose.yml). All chat requests
are sent to the agent’s `/v1/chat/completions` endpoint, which then relays to
LiteLLM, Ollama, and the retrieval services (embedder + Qdrant) managed by
`ragproxy`. LiteLLM remains available for tooling and external experiments, but
end-user traffic is mediated by the agent.

## Health Checks

- Each container defines a Compose healthcheck, enabling dependency ordering
  and reliable readiness detection.
- `python -m stack status` reports the Docker health state for all containers.
- `/healthz` endpoint on the agent service is used by Compose and external
  monitoring.
