# Retrieval-Augmented Generation Flow

The retrieval stack enriches language model prompts with curated context before
producing a final answer. It spans five services and a shared vector store:

- **webfetch** – fetches and summarises web pages, optionally powered by
  SearxNG for metasearch queries.
- **indexer** – CLI utility that orchestrates bulk ingestion, chunking, and
  vector upserts into Qdrant.
- **embedder** – FastAPI service that exposes `/embed` for deterministic
  sentence-transformer embeddings.
- **qdrant** – vector database that stores chunk payloads alongside embeddings.
- **ragproxy** – user-facing chat completion proxy that retrieves memories,
  applies Maximal Marginal Relevance (MMR), and forwards augmented prompts to
  LiteLLM.

The agent itself can call `web_fetch` or directly query Qdrant, but the contract
below describes the primary ingest → embed → store → retrieve → re-rank →
respond flow that underpins `rag/` models. Agent memory writes use the same
embedder service so the stored vectors stay compatible with ragproxy's retrievals.

## Pipeline

1. **Ingest** – Operators run `python -m indexer.ingest <urls...>` (see
   [`indexer/ingest.py`](../../indexer/ingest.py)) to fetch raw HTML via
   `/extract` on `webfetch`. The indexer trims boilerplate, chunks text into
   ~800-character windows with 100-character overlap, and tags each chunk with
   the source URL and timestamp.
2. **Embed** – The indexer sends the prepared chunks to the embedder service at
   `POST /embed` with `{"inputs": [...], "normalize": true}`. The embedder
   loads the HuggingFace model defined by `MODEL_NAME` (default
   `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) and returns a
   JSON payload:
   ```json
   {
     "vectors": [[0.01, ...]],
     "normalize": true,
     "dim": 384
   }
   ```
   The same endpoint is used by `webfetch` for on-demand semantic ranking.
3. **Store** – The indexer calls Qdrant’s `/collections/{name}/points` API (via
   the Python client) to upsert each chunk as a vector with payload metadata
   (`url`, `text`, `chunk_ix`, `ts`, `source`). The default collection is named
  `memory`; see [`docker-compose.yml`](../../docker-compose.yml) for the
  persistent volume wiring.
4. **Retrieve** – When a client submits a `rag/` model to `ragproxy` (for
  example `rag/phi3-en`), the proxy embeds the last user message with the
   same `/embed` API and queries Qdrant’s `/collections/memory/points/search`
   endpoint for the top candidates. `QDRANT_TOP_K` controls the fan-out before
   reranking.
5. **Re-rank** – Ragproxy converts the raw vectors to NumPy arrays and applies
   Maximal Marginal Relevance (MMR) via `_mmr(...)`. `MMR_LAMBDA` tunes the
   diversity (0 → purely similarity, 1 → purely diversity). Duplicated URLs are
   removed before scoring. The resulting hits are truncated to `RAG_MAX_SOURCES`
   and `RAG_MAX_CHARS` to keep prompts bounded.
6. **Respond** – The proxy rewrites the chat request to a constrained system
  prompt plus a single user message that embeds the question, formatted context
  (`Source [n] URL` blocks), and a numbered source list. The modified payload is
  forwarded to LiteLLM (default `local/phi3-en`). LiteLLM streams the completion
  back unchanged so the agent or Open WebUI can post-process (e.g., translate to
  Swedish) before presenting it to users.
   or Open WebUI receive a response whose assistant message cites `[n]` markers
   aligned with the injected sources.

SearxNG is invoked only when a tool or workflow triggers `/search` on
`webfetch`. Search hits can be summarised and embedded into Qdrant, enabling
future ragproxy calls to surface them without repeating the crawl.

## Component Interfaces

### Embedder API

- `GET /health` → `{ "ok": true, "model": "..." }`
- `POST /embed` → accepts an `inputs` list of strings and optional `normalize`
  flag. Responses include `vectors`, `normalize`, and `dim`. Downstream services
  assume 384-dimensional cosine-normalised outputs.

### Ragproxy API

- `POST /v1/chat/completions` mirrors the OpenAI schema. Selecting a model that
  starts with `rag/` enables retrieval. All other models are forwarded directly
  to LiteLLM without modification.
- `GET /health` → `{ "ok": true }`

### Qdrant Usage

- Collection name: `memory`
- Vector size: 384, cosine distance
- Payload keys used by ragproxy: `url` (string) and `text` (string)
- Requests come from both `ragproxy` and `webfetch` using the official HTTP
  client and REST endpoints. Consult [`docs/OPERATIONS.md`](../OPERATIONS.md)
  for backup and migration guidance.

### SearxNG & Webfetch

- `webfetch` proxies SearxNG via `GET /search` when `SEARXNG_URL` is configured.
  Search results feed summarisation jobs that in turn populate Qdrant.
- Direct content extraction uses `POST /extract` (batch HTML retrieval) and
  `POST /summarize` (LiteLLM-backed synthesis). Refer to
  [`fetcher/app.py`](../../fetcher/app.py) for the specific endpoints exposed to
  agent tools.

## Configuration

| Variable | Purpose | Default | Defined in |
| --- | --- | --- | --- |
| `ENABLE_RAG` | Toggles context injection in ragproxy. | `true` | [`ragproxy/app.py`](../../ragproxy/app.py) |
| `QDRANT_TOP_K` | Number of nearest neighbours fetched before MMR rerank. | `5` | [`ragproxy/app.py`](../../ragproxy/app.py), [`fetcher/app.py`](../../fetcher/app.py) |
| `MMR_LAMBDA` | Balance between similarity and diversity during MMR. | `0.7` | [`ragproxy/app.py`](../../ragproxy/app.py), [`fetcher/app.py`](../../fetcher/app.py) |
| `RAG_MAX_SOURCES` | Maximum number of sources added to the prompt. | `5` | [`ragproxy/app.py`](../../ragproxy/app.py) |
| `RAG_MAX_CHARS` | Characters retained per source snippet. | `1200` | [`ragproxy/app.py`](../../ragproxy/app.py) |
| `EMBEDDER_BASE` | Base URL for embedding requests. | `http://embedder:8082` | [`ragproxy/app.py`](../../ragproxy/app.py), [`fetcher/app.py`](../../fetcher/app.py) |
| `QDRANT_URL` | Vector store endpoint. | `http://qdrant:6333` | [`ragproxy/app.py`](../../ragproxy/app.py), [`fetcher/app.py`](../../fetcher/app.py) |
| `MODEL_NAME` | HuggingFace model used by embedder. | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | [`embedder/app.py`](../../embedder/app.py) |
| `AGENT_EMBEDDER_URL` | Agent configuration used to find the embedder service for memory operations. | `http://embedder:8082` | [`src/agent/core/config.py`](../../src/agent/core/config.py) |
| `AGENT_QDRANT_VECTOR_SIZE` | Vector size used when the agent creates or normalises the `agent-memories` collection. | `384` | [`src/agent/core/config.py`](../../src/agent/core/config.py) |

Override these variables in `.env` before running `python -m stack up`.
Operational playbooks in [`docs/OPERATIONS.md`](../OPERATIONS.md) cover health
checks and smoke tests for the retrieval services.

## Example: RAG Chat Request

### 1. Client → Ragproxy

```json
POST /v1/chat/completions
{
  "model": "rag/phi3-en",
  "messages": [
    {"role": "user", "content": "Summarise the latest Qdrant release."}
  ]
}
```

### 2. Ragproxy → LiteLLM (augmented payload)

```json
{
    "model": "local/phi3-en",
  "messages": [
    {
      "role": "system",
      "content": "You are a precise assistant. Use ONLY the provided context for facts. Cite as [n] and list sources."
    },
    {
      "role": "user",
      "content": "Question: Summarise the latest Qdrant release.\n\nContext:\nSource [1] https://qdrant.tech/blog/qdrant-1-9/\nQdrant 1.9 adds hybrid search...\n\nSource [2] https://qdrant.tech/changelog/\nHighlights include performance improvements...\n\nSources:\n- [1] https://qdrant.tech/blog/qdrant-1-9/\n- [2] https://qdrant.tech/changelog/\n\n"
    }
  ]
}
```

### 3. LiteLLM → Client (via Ragproxy)

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Qdrant's latest release focuses on hybrid search throughput and faster filters [1][2].\n\nSources\n- [1] https://qdrant.tech/blog/qdrant-1-9/\n- [2] https://qdrant.tech/changelog/"
      }
    }
  ],
    "model": "rag/phi3-en"
}
```

Tests that assert the prompt contract should confirm that numbered sources in
`Context` align with the citations returned by the assistant message.
