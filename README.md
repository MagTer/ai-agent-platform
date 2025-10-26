# AI Agent Platform (Local, Containerized)

> **For AI assistants (Codex) — Start here.**  
> All project documentation lives under [`/docs`](./docs). This root README links the essentials so you can locate **vision, constraints, delivery model (2–3h steps), roadmap, capabilities, architecture, operations, and runbooks** in one click.

---

## Quick Start (Local)

```powershell
# 1) Copy environment and edit if needed
cp .env.template .env

# 2) Bring the stack up (PowerShell)
.\scripts\Stack-Up.ps1

# 3) Smoke tests
# - Webfetch health
irm http://localhost:8081/health
# - Actions orchestrator health
irm http://localhost:5678/healthz
# - Research end-to-end (POST JSON)
$b=@{ query="Summarize pros/cons of Qdrant (Swedish)"; k=2; lang="sv" } | ConvertTo-Json -Compress
irm http://localhost:8081/research -Method POST -ContentType 'application/json' -Body $b
```

> Models: Vid uppstart ser skriptet till att basmodellen `qwen2.5:14b-instruct-q4_K_M` finns i Ollama och skapar automatiskt den svenska profilen `qwen2.5-sv` från `ollama/models/qwen2.5-sv.modelfile` om den saknas. I LiteLLM exponeras den som `local/qwen2.5-sv`.

## n8n Workflow Sync

Version-controlled backups of the automation workflows live in [`flows/`](./flows). Use the PowerShell helper [`scripts/N8N-Workflows.ps1`](./scripts/N8N-Workflows.ps1) to keep the repository and the running n8n instance in sync:

1. The script verifies that the `n8n` container is running before attempting any action.
2. `export` runs `n8n export:workflow` inside the container, copies the result into `flows/workflows.json`, and regenerates one JSON file per workflow under `flows/workflows/` (removing any stale files).
3. `import` bundles the JSON files from `flows/workflows/` (or the combined file as a fallback) and feeds them back into `n8n import:workflow` for a clean restore.
4. Add `-IncludeCredentials` to include `flows/credentials.json` in both directions when you intentionally want to version-control secrets.

> Tip: Edit workflows directly in the n8n UI, then run `export` so the repo stays up to date. See [`flows/README.md`](./flows/README.md) for examples.

## Open WebUI Config Sync

Open WebUI stores its state in a SQLite database. The stack now mounts
[`openwebui/data`](./openwebui/data) into the container so the UI is
reproducible between restarts, while the helper script
[`scripts/OpenWebUI-Config.ps1`](./scripts/OpenWebUI-Config.ps1) converts the
database into a text dump for git:

```powershell
# Dump the current UI config (tools, presets, settings) to openwebui/export/app.db.sql
./scripts/OpenWebUI-Config.ps1 export

# Restore the running instance from the dump
./scripts/OpenWebUI-Config.ps1 import
```

Commit the SQL dump after adding tools (e.g., the `n8n_action` REST hook) so the
automation remains reproducible.
