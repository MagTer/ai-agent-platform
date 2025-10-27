# Operations

## Start
```powershell
# From repo root; env lives in compose/
cp compose\.env.template compose\.env
./scripts/Stack-Up.ps1              # start
# or rebuild changed images then start
./scripts/Stack-Up.ps1 -Build
```

Notes
- `Stack-Up.ps1` requires `OPENWEBUI_SECRET` and `SEARXNG_SECRET` in `compose/.env`.
- Models: the script ensures `qwen2.5:14b-instruct-q4_K_M` exists in Ollama. The Swedish profile is provided via LiteLLM alias `local/qwen2.5-sv` (no separate Ollama model).
 - Model intent can be declared in `config/models.txt` (one model per line). `Stack-Up.ps1` reads this file and ensures listed models are present.

## Stop
```powershell
./scripts/Stack-Down.ps1 -KeepVolumes   # keep models/data
```

## Health & Logs
```powershell
# Health dashboard (checks key endpoints)
./scripts/Stack-Health.ps1

# Compose status
docker compose -f compose\docker-compose.yml ps

# Logs (tail)
./scripts/Stack-Logs.ps1                 # core services
./scripts/Stack-Logs.ps1 -Service n8n    # single service
```

## Bind Mount Mode (optional)

Use bind mounts for Ollama and Qdrant persistent data instead of named volumes:

```powershell
./scripts/Stack-Up.ps1 -BindMounts
```

This uses `compose/docker-compose.bind.yml` to map host directories:
- `.data/ollama` -> `/root/.ollama`
- `.data/qdrant` -> `/qdrant/storage`

Rollback: start without `-BindMounts` to return to named volumes. Do not delete named volumes until you have verified stability.

### Migrate existing data to bind mounts

Ollama (copy models from the named volume to host):
```powershell
# Stop ollama first if needed
docker compose -f compose\docker-compose.yml stop ollama
# Copy data out of the volume to ./.data/ollama
docker run --rm --volumes-from ollama -v ${PWD}/.data/ollama:/host alpine sh -lc "mkdir -p /host && cp -a /root/.ollama/. /host/"
```

Qdrant (use the provided scripts to snapshot/restore):
```powershell
./scripts/Qdrant-Backup.ps1
# Stop qdrant, then restore the snapshot into ./.data/qdrant on host (optional enhancement forthcoming)
./scripts/Qdrant-Restore.ps1 -BackupFile .\backups\qdrant-YYYYMMDD-HHMMSS.tgz
```

## Smoke Tests
```powershell
# Research pipeline
$b=@{ query="RAG in customer support"; k=2; lang="en" } | ConvertTo-Json -Compress
irm http://localhost:8081/research -Method POST -ContentType 'application/json' -Body $b

# Retrieval debug (memory + web hits without LLM)
irm "http://localhost:8081/retrieval_debug?q=Qdrant" | Format-List *

# Show only memory hits (url + short snippet)
$r = irm "http://localhost:8081/retrieval_debug?q=Qdrant"
$r.memory | Select-Object url, @{n='snippet';e={$_.text.Substring(0, [Math]::Min(80, $_.text.Length))}}

# Raw JSON
irm "http://localhost:8081/retrieval_debug?q=Qdrant" | ConvertTo-Json -Depth 6

# Actions echo
$payload=@{ action="agent.echo"; args=@{ message="ping" } } | ConvertTo-Json -Compress
irm http://localhost:5678/webhook/agent -Method POST -ContentType 'application/json' -Body $payload
```

Expected response (JSON): `{ "ok": true, "action": "agent.echo", "received": { ... }, "timestamp": "..." }`

## Test models via LiteLLM

English profile:
```bash
curl -sS -X POST http://localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "local/qwen2.5-en",
    "messages": [
      {"role":"user","content":"Write two concise sentences in English about how this model is used in the platform."}
    ],
    "temperature": 0.35
  }'
```

Swedish profile:
```bash
curl -sS -X POST http://localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "local/qwen2.5-sv",
    "messages": [
      {"role":"user","content":"Skriv två meningar på svenska om hur modellen används i plattformen."}
    ],
    "temperature": 0.4
  }'
```

## RAG via LiteLLM (ragproxy)

Configured via `compose/.env`:
- `ENABLE_RAG=true|false` - global on/off for server-side RAG in `ragproxy`.
- `RAG_MAX_SOURCES=5` - max number of sources to inject.
- `RAG_MAX_CHARS=1200` - max chars per source.

Test:
```bash
curl -sS -X POST http://localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "rag/qwen2.5-sv",
    "messages": [
      {"role":"user","content":"Sammanfatta huvudpunkter om n8n från kända källor på svenska och ange källor."}
    ],
    "temperature": 0.3
  }'
```

## Common Issues
- 403 from SearxNG -> set `SEARXNG_BASE_URL=http://searxng:8080/` inside the container (compose already sets internal host).
- LiteLLM flags -> container entrypoint provides the binary; `command:` must contain only flags (e.g., `--config /app/config.yaml --port 4000`).
- GPU not visible -> `docker exec -it ollama nvidia-smi`; ensure NVIDIA runtime/driver present.

## Maintenance
- Use `scripts/Repo-Save.ps1` to commit edits.
- Rebuild only the changed service:
```powershell
docker compose -f compose\docker-compose.yml build webfetch
docker compose -f compose\docker-compose.yml up -d webfetch
```

## n8n Backups & Restore
```powershell
# Export all workflows to repo (creates/updates flows/workflows/*.json and flows/workflows.json)
./scripts/N8N-Workflows.ps1 export

# Import workflows from repo into running n8n
./scripts/N8N-Workflows.ps1 import

# Include credentials (stored in flows/credentials.json)
./scripts/N8N-Workflows.ps1 export -IncludeCredentials
./scripts/N8N-Workflows.ps1 import -IncludeCredentials
```

> Be careful with secrets if you choose to include credentials in git.

## Open WebUI Config Dumps
```powershell
# Save current Open WebUI database as SQL dump (tools, presets, settings)
./scripts/OpenWebUI-Config.ps1 export

# Restore the UI from the last exported dump
./scripts/OpenWebUI-Config.ps1 import
```

> Import overwrites `/app/backend/data/app.db`. Stop or idle the container to avoid locking.

## Qdrant Backups & Restore
```powershell
# Create snapshot of Qdrant storage (tgz in ./backups)
./scripts/Qdrant-Backup.ps1

# Restore from a specific backup file
./scripts/Qdrant-Restore.ps1 -BackupFile .\backups\qdrant-YYYYMMDD-HHMMSS.tgz
```

> Backup/restore uses a temporary Alpine container with `--volumes-from qdrant` to read/write `/qdrant/storage`.

## Ingestion (example)
```powershell
# Index one or more URLs into Qdrant (collection: memory)
python .\indexer\ingest.py "https://example.com" "https://example.org"

# Then the Qdrant memory influences research replies via retrieval
```

Host setup (if Python deps are missing)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r indexer\requirements.txt
python .\indexer\ingest.py "https://example.com" "https://example.org"
```

## RAG Settings (webfetch)
- `ENABLE_QDRANT=true|false` - toggle memory retrieval in `webfetch`.
- `QDRANT_TOP_K=5` - number of memory hits to blend.
- `MMR_LAMBDA=0.7` - relevance vs diversity (closer to 1 = more relevance).

## Qdrant Schema Ensure
Use the helper to ensure the `memory` collection exists with desired vector settings:
```powershell
./scripts/Qdrant-EnsureSchema.ps1 -Collection memory -Size 384 -Distance Cosine
```
