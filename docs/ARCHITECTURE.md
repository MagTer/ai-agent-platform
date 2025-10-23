# Architecture

## Services (MVP)
- **ollama (GPU)** — local models: `llama3:8b` (EN), `ai-sweden-gpt-sw3:6.7b` (SV)
- **litellm** — routing, budgets, rate limits (→ OpenRouter optional)
- **openwebui** — client (presets: Reasoning, Research, Actions)
- **searxng** — meta-search
- **webfetch** — FastAPI: search → extract → summarize via LiteLLM
- **qdrant** — vector database
- **n8n** *(planned/next)* — action orchestrator (webhook “Single Wrapper”)

## Default Ports (override via `.env`)
OPENWEBUI=3000 · LITELLM=4000 · QDRANT=6333 · OLLAMA=11434 · SEARXNG=8080 · FETCHER=8081 · N8N=5678

## Data & Persistence
- Models: `data/ollama` (bind mount) or named volume with `-KeepVolumes`.
- n8n data: `data/n8n`.
- Backups: scripts under `/scripts/` (planned).

## Flows

### Research
Open WebUI → (tool) `research_web` → `webfetch` → SearxNG + extraction → LiteLLM → Ollama → concise summary + sources.

### Actions (target)
Open WebUI → (tool) `n8n_action` → n8n `/webhook/agent` → capability mapping → provider flow (GitHub/ADO/M365/…).

## Security Notes
- Keep all secrets in `.env` (not committed).
- If exposing UIs, prefer Cloudflare/Tailscale or Zero Trust headers.
- Webhooks (n8n) should be internal-only unless secured.
