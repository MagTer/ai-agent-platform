# Architecture

## Services (MVP)
- ollama (GPU): local model `qwen2.5:14b-instruct-q4_K_M`
- litellm: routing, budgets, and rate limits (OpenRouter optional)
- openwebui: client (presets: Reasoning, Research, Actions)
- searxng: meta-search
- webfetch: FastAPI (search -> extract -> summarize via LiteLLM)
- qdrant: vector database
- embedder (CPU): sentence-transformers (MiniLM L12 v2, 384d) for embeddings
- n8n: action orchestrator (webhook "Single Wrapper")
- ragproxy: OpenAI-compatible pre-processor injecting Qdrant hits before LiteLLM

## Default Ports (override via `compose/.env`)
OPENWEBUI=3000, LITELLM=4000, QDRANT=6333, OLLAMA=11434, SEARXNG=8080, FETCHER=8081, N8N=5678

## Data & Persistence
- Models: named volume `ollama_data` (migration to bind mounts is planned; see ROADMAP).
- n8n data: named volume `n8n_data` (`/home/node/.n8n`).
- Backups: scripts under `/scripts/`.

## Flows

### Research
Open WebUI -> LiteLLM (`rag/qwen2.5-*`) -> ragproxy ->
  - Query embeddings (embedder, CPU) -> Qdrant retrieval (MMR + dedup)
  - Context injection into prompt (EN/SV profiles)
-> LiteLLM -> Ollama/Qwen -> answer with sources.

Alternative (parallel support):
Open WebUI -> (tool) `research_web` -> `webfetch` (web + memory fusion) -> LiteLLM -> Ollama.

### Actions (target)
Open WebUI -> (tool) `n8n_action` -> n8n `/webhook/agent` -> capability mapping -> provider flow (GitHub/ADO/M365/...).
Baseline: `agent_echo` workflow (see `flows/workflows.json`) returns an acknowledgement with timestamp.

## Workflow Lifecycle (n8n)
1. Create or modify workflows in the n8n UI (persisted in `n8n_data`).
2. Export artifacts for version control using `scripts/N8N-Workflows.ps1 export`.
   - Exports per-workflow JSON to `flows/workflows/` and a combined `flows/workflows.json`.
3. Commit the exported JSON files to keep history.
4. Restore using `scripts/N8N-Workflows.ps1 import` after stack bootstrap.

> Alternative: evaluate n8n git integration once non-local deployments are needed.

## Security Notes
- Keep all secrets in `compose/.env` (not committed).
- If exposing UIs, prefer Cloudflare/Tailscale or Zero Trust.
- Webhooks (n8n) should be internal-only unless secured.

