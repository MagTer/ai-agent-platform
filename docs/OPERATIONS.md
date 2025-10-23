# Operations

## Start
```powershell
cp .env.template .env
.\scripts\Stack-Up.ps1
```

> **Models**: `Stack-Up.ps1` currently ensures the Ollama models `llama3:8b` and `fcole90/ai-sweden-gpt-sw3:6.7b` are present in the `ollama` container.

## Stop
```powershell
.\scripts\Stack-Down.ps1 -KeepVolumes   # keep models/data
```

## Health & Logs
```powershell
# Health
irm http://localhost:8081/health
irm http://localhost:5678/healthz

# Compose status
docker compose -f compose\docker-compose.yml ps

# Logs
.\scripts\Stack-Logs.ps1 -Service webfetch
.\scripts\Stack-Logs.ps1 -Service n8n
```

## Smoke Tests
```powershell
# Research pipeline
$b=@{ query="RAG i kundsupport"; k=2; lang="sv" } | ConvertTo-Json -Compress
irm http://localhost:8081/research -Method POST -ContentType 'application/json' -Body $b

# Actions echo
$payload=@{ action="agent.echo"; args=@{ message="ping" } } | ConvertTo-Json -Compress
irm http://localhost:5678/webhook/agent -Method POST -ContentType 'application/json' -Body $payload
```

> Förväntat svar (JSON): `{"ok":true,"action":"agent.echo","received":{"args":{"message":"ping"},"raw":{"action":"agent.echo","args":{"message":"ping"}}},"meta":{"headers":{...},"query":{...}},"timestamp":"..."}`

## Common Issues
- **403 from SearxNG** → set `BASE_URL=http://searxng:8080/` in compose (internally consistent host).
- **LiteLLM “unexpected extra argument (litellm)”** → the container’s entrypoint provides the binary; `command:` must contain only flags (e.g., `--config /app/config.yaml --port 4000`).
- **GPU not visible** → `docker exec -it ollama nvidia-smi`; ensure NVIDIA runtime/driver present.

## Maintenance
- Use `scripts/Repo-Save.ps1` to commit edits.
- Rebuild only the changed service:
```powershell
docker compose -f compose\docker-compose.yml build webfetch
docker compose -f compose\docker-compose.yml up -d webfetch
```

## n8n Backups & Restore
```powershell
# Export all workflows & credentials to mounted volume
docker exec n8n mkdir -p /home/node/.n8n/export
docker exec n8n n8n export:workflow --all --output /home/node/.n8n/export/workflows.json
docker exec n8n n8n export:credentials --all --output /home/node/.n8n/export/credentials.json

# Copy exports to host repo (e.g., ./flows)
docker cp n8n:/home/node/.n8n/export .\flows

# Restore after clean environment
docker cp .\flows\workflows.json n8n:/home/node/.n8n/import-workflows.json
docker exec n8n n8n import:workflow --input /home/node/.n8n/import-workflows.json --separate
docker cp .\flows\credentials.json n8n:/home/node/.n8n/import-credentials.json
docker exec n8n n8n import:credentials --input /home/node/.n8n/import-credentials.json
```

> Tips: keep exports in git (with secrets redacted) and rely on the `n8n_data` volume for quick local recovery. Consider scripting these steps once we stabilise the workflow catalogue.
