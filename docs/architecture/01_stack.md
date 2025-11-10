# Runtime Stack

## Compose Services

[`docker-compose.yml`](../../docker-compose.yml) in the repository root now
defines the complete stack, including the GPU runtime settings that Ollama
requires. Compose automatically reads `.env`, so set the variables you need
there (or export them in your shell) before running `python -m stack up`.

| Service     | Purpose | Ports | Healthcheck |
|-------------|---------|-------|-------------|
| `openwebui` | Web UI frontend that proxies chat through the agent | `3000 → 8080` | `curl -f http://localhost:8080` |
| `agent`     | FastAPI agent orchestrator and tool runner | `8000` | `curl -f http://localhost:8000/healthz` |
| `litellm`   | Gateway to local/remote LLMs | `4000` | `curl -f http://localhost:4000/health` |
| `ollama`    | GPU accelerated inference engine | `11434` | `ollama --version` |
| `qdrant`    | Vector memory for semantic recall | `6333` | `wget -qO- http://localhost:6333/healthz` |
| `embedder`  | Sentence-transformer API producing `/embed` vectors for RAG | `8082` | `wget -qO- http://localhost:8082/health` |
| `ragproxy`  | Retrieval-aware proxy that augments `rag/` chat completions | `4080` (internal) | `wget -qO- http://localhost:4080/health` |
| `webfetch`  | Browserless content retriever and summariser | `8081` | `wget -qO- http://localhost:8081/health` |
| `searxng`   | Optional metasearch backend queried by `webfetch` | `8080` | `wget -qO- http://localhost:8080/` |
| `n8n`       | Automation/workflow runner that integrates tools | `5678` | `wget -qO- http://localhost:5678/healthz` |

Volumes:

- `ollama-models` stores downloaded Ollama models.
- `qdrant-data` keeps vector data between restarts.
- `embedder-cache` caches HuggingFace artefacts for the embedder.
- `n8n-data` persists n8n workflows and credentials.

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
live in `.env.local` or direct environment exports. Compose automatically reads
`.env`; the stack CLI also injects it when running Docker commands.

Open WebUI is wired to the agent by default via the `LITELLM_URL` and
`OPENAI_API_BASE_URL` variables in `docker-compose.yml`. All chat requests are
sent to the agent’s `/v1/chat/completions` endpoint, which then relays to
LiteLLM and Ollama. LiteLLM remains available for tooling and external
experiments, but end-user traffic is mediated by the agent. Retrieval-aware
deployments also route `rag/` model traffic through `ragproxy`; see
[`docs/architecture/06_rag.md`](./06_rag.md) for the ingest → respond pipeline and
configuration defaults.

## Health Checks

- Each container defines a Compose healthcheck, enabling dependency ordering
  and reliable readiness detection.
- `python -m stack status` reports the Docker health state for all containers.
- `/healthz` endpoint on the agent service is used by Compose and external
  monitoring.
