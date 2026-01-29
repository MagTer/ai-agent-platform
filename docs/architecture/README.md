# Architecture Overview

The AI Agent Platform is a self-hosted research and automation environment designed for reliable operation. A Python-based agent service built on FastAPI and LiteLLM coordinates tools, memory, and LLM providers while Docker Compose delivers consistent deployments.

## System Topology

```
+-------------------+       +-------------------+       +-------------------+
|   Open WebUI      | <-->  |  Agent (FastAPI)  | <-->  |     LiteLLM       |
|  (Reasoning UI)   |       |  Tools + Memory   |       |  OpenRouter GW    |
+-------------------+       +-------------------+       +-------------------+
                                 |            \
                                 |             \
                           +-------------+   +-------------+
                           |   Qdrant     |   |  Postgres   |
                           | Vector DB    |   | Conversation|
                           +-------------+   |   Storage   |
                                              +-------------+
                                 |
                                 v
                           +-------------+
                           |   SearXNG   |
                           |  Web Search |
                           +-------------+
```

## Key Principles

- **Python First** – The agent service, orchestration CLI, and developer workflow are all Python-based and managed with Poetry.
- **Composable Services** – Each component runs in its own container, enabling targeted upgrades and debugging.
- **Observability** – Health checks, Stack CLI status reporting, and deterministic logging simplify operations.

## Service Map

| Service | Role | Default Ports |
| --- | --- | --- |
| `agent` | FastAPI orchestration layer exposing `/healthz`, `/v1/agent`, and OpenAI-compatible `/v1/chat/completions`. | `8000` |
| `stack CLI` | Typer-based helper (`python -m stack`) that wraps Docker Compose for lifecycle management and health reporting. | n/a |
| `litellm` | Gateway that proxies chat completions to OpenRouter. | `4000` |
| `openwebui` | Web UI for reasoning modes; routes chat traffic through the agent. | `3000 → 8080` |
| `qdrant` | Vector store that retains semantic memories and RAG content chunks. | `6333` |
| `postgres` | Database for conversation and context storage. | `5432` |
| `searxng` | Metasearch backend supplying URLs for web search. | `8080` |

## Related Documents

| File | Focus |
| --- | --- |
| `01_stack.md` | Docker Compose services, volumes, and stack CLI usage. |
| `02_agent.md` | Agent modules, request lifecycle, and dependency graph. |
| `03_tools.md` | Tool registry, configuration (`config/tools.yaml`), and testing patterns. |
| `04_dev_practices.md` | Coding standards, Poetry workflow, linting, and typing. |

## Configuration & Data

- `.env` (root) – loaded by both the stack CLI and FastAPI via `python-dotenv`.
- `config/tools.yaml` – declarative registration of tool classes exposed to the agent.
- Volumes: `qdrant-data` and `postgres_data` persist vector and conversation storage.

## Security Notes

- Secrets remain in the uncommitted `.env` file; sample keys belong in `.env.template`.
- Internal services communicate over the Docker network; expose only Open WebUI or the agent externally with proper auth.
- OpenRouter API keys should be rotated periodically and kept in `.env`.

## Update Checklist

When changing any service or contract, synchronise the relevant `docs/architecture/*.md` file, update `docs/OPERATIONS.md` with new commands, and review CI expectations in `docs/testing/01_ci.md`.
