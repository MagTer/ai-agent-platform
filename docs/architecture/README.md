# Architecture Overview

The AI Agent Platform is a self-hosted research and automation environment designed for deterministic local operation. A Python-based agent service built on FastAPI and LiteLLM coordinates tools, memory, and inference providers while Docker Compose delivers consistent deployments.

## System Topology

```
+-------------------+       +-------------------+       +-------------------+
|   Open WebUI      | <-->  |  Agent (FastAPI)  | <-->  |     LiteLLM       |
|  (Reasoning UI)   |       |  Tools + Memory   |       |  Gateway to LLMs  |
+-------------------+       +-------------------+       +-------------------+
                                 |            \
                                 |             \
                           +-------------+   +-------------+
                           |   Qdrant     |   |   SQLite    |
                           | Vector DB    |   | Conversation|
                           +-------------+   |   Metadata  |
                                              +-------------+
                                 |
                                 v
                           +-------------+
                           |  Webfetch   |
                           |  Retrieval  |
                           +-------------+
```

## Key Principles

- **Python First** – The agent service, orchestration CLI, and developer workflow are all Python-based and managed with Poetry.
- **Composable Services** – Each component runs in its own container, enabling targeted upgrades and debugging.
- **Observability** – Health checks, Stack CLI status reporting, and deterministic logging simplify operations.

## Service Map

| Service | Role | Default Ports |
| --- | --- | --- |
| `agent` | FastAPI orchestration layer (`src/agent/`) exposing `/healthz`, `/v1/agent`, and OpenAI-compatible `/v1/chat/completions`. | `8000` |
| `stack CLI` | Typer-based helper (`python -m stack`) that wraps Docker Compose for lifecycle management and health reporting. | n/a |
| `litellm` | Gateway that proxies chat completions to Ollama or remote providers configured in Compose. | `4000` |
| `ollama` | GPU-capable inference runtime that serves Phi3 Mini (via `runtime: nvidia` and the Nvidia driver caps) | `11434` |
| `openwebui` | Web UI for reasoning modes; routes chat traffic through the agent. | `3000 → 8080` |
| `qdrant` | Vector store that retains semantic memories and RAG content chunks. | `6333` |
| `embedder` | CPU-bound sentence-transformer service providing deterministic `/embed` vectors. | `8082` |
| `ragproxy` | Retrieval-aware proxy that injects context and calls LiteLLM when `rag/` models are requested. | `4080` (internal) |
| `webfetch` | Content retriever, summariser, and ingestion helper used by tools and the indexer CLI. | `8081` |
| `searxng` | Optional metasearch backend supplying URLs for `webfetch` ingestion. | `8080` |

RAG responsibilities for `embedder`, `ragproxy`, `qdrant`, `webfetch`, and `searxng`
are described in detail in [`docs/architecture/06_rag.md`](./06_rag.md).

## Related Documents

| File | Focus |
| --- | --- |
| `01_stack.md` | Docker Compose services, volumes, and stack CLI usage. |
| `02_agent.md` | Agent modules, request lifecycle, and dependency graph. |
| `03_tools.md` | Tool registry, configuration (`config/tools.yaml`), and testing patterns. |
| `04_dev_practices.md` | Coding standards, Poetry workflow, linting, and typing. |
| [`06_rag.md`](./06_rag.md) | Retrieval pipeline (ingest → embed → store → retrieve → re-rank → respond). |
| [`docs/testing/01_ci.md`](../testing/01_ci.md) | GitHub Actions pipeline (lint, coverage, and compose validation). |

## Configuration & Data

- `.env` (root) – loaded by both the stack CLI and FastAPI via `python-dotenv`.
- `config/tools.yaml` – declarative registration of tool classes exposed to the agent.
- Volumes: `ollama-models` and `qdrant-data` persist models and vector storage.
- SQLite database defaults to `data/agent_state.sqlite` (mounted into the agent container) for conversation metadata.

## Security Notes

- Secrets remain in the uncommitted `.env` file; sample keys belong in `.env.template`.
- Internal services communicate over the Docker network; expose only Open WebUI or the agent externally with proper auth.
- Keep LiteLLM routing rules minimal by default; document any premium provider usage in `docs/architecture/03_tools.md`.

## Update Checklist

When changing any service or contract, synchronise the relevant `docs/architecture/*.md` file, update `docs/OPERATIONS.md` with new commands, and review CI expectations in `docs/testing/01_ci.md`.
