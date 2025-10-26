# Architecture

## Services (MVP)
- **ollama (GPU)** — local models: `llama3:8b` (EN), `ai-sweden-gpt-sw3:6.7b` (SV)
- **litellm** — routing, budgets, rate limits (→ OpenRouter optional)
- **openwebui** — client (presets: Reasoning, Research, Actions)
- **searxng** — meta-search
- **webfetch** — FastAPI: search → extract → summarize via LiteLLM
- **qdrant** — vector database
 - **embedder (CPU)** — sentence-transformers (MiniLM L12 v2, 384d) for embeddings
 - **n8n** — action orchestrator (webhook “Single Wrapper”)
 - **ragproxy** — OpenAI‑kompatibel pre‑processor som injicerar Qdrant‑träffar före LiteLLM

## Default Ports (override via `compose/.env`)
OPENWEBUI=3000 · LITELLM=4000 · QDRANT=6333 · OLLAMA=11434 · SEARXNG=8080 · FETCHER=8081 · N8N=5678

## Data & Persistence
- Models: `data/ollama` (bind mount) or named volume with `-KeepVolumes`.
- n8n data: named volume `n8n_data` (`/home/node/.n8n`).
- Backups: scripts under `/scripts/` (planned).

## Flows

### Research
Open WebUI → LiteLLM (`rag/qwen2.5-*`) → ragproxy →
  - Query embeddings (embedder, CPU) → Qdrant retrieval (MMR + dedup)
  - Injektion av kontext i prompt (sv/en profil)
→ LiteLLM → Ollama/Qwen → svar med källor.

Alt (parallellt stöd):
Open WebUI → (tool) `research_web` → `webfetch` (web + memory fusion) → LiteLLM → Ollama.

### Actions (target)
Open WebUI → (tool) `n8n_action` → n8n `/webhook/agent` → capability mapping → provider flow (GitHub/ADO/M365/…).
Baseline idag: `agent_echo`-workflowen (se `flows/workflows.json`) svarar med kvittens och tidsstämpel.

## Workflow Lifecycle (n8n)
1. **Create or modify** workflows in the n8n UI (persisted in `n8n_data`).
2. **Export** artefacts for version control:
   ```bash
   docker exec n8n n8n export:workflow --all --output /home/node/.n8n/export/workflows.json
   docker exec n8n n8n export:credentials --all --output /home/node/.n8n/export/credentials.json
   docker cp n8n:/home/node/.n8n/export ./flows
   ```
3. **Commit** the exported JSON files under a tracked `flows/` directory to keep history.
4. **Restore** using `n8n import:workflow --input flows/workflows.json` (and credential import) after stack bootstrap.

> _Alternative:_ evaluate [n8n git integration](https://docs.n8n.io/hosting/git-integration/) once we need collaborative editing or non-local deployments. For now, explicit export/import keeps secrets isolated and reproducible.

## Security Notes
- Keep all secrets in `compose/.env` (not committed).
- If exposing UIs, prefer Cloudflare/Tailscale or Zero Trust headers.
- Webhooks (n8n) should be internal-only unless secured.
