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
# Exportera alla workflows till repo:t (skapar/uppdaterar flows/workflows/*.json)
.\scripts\N8N-Workflows.ps1 export

# Importera workflows från repo:t till körande n8n-container
.\scripts\N8N-Workflows.ps1 import

# Ta även med credentials (lagras i flows/credentials.json)
.\scripts\N8N-Workflows.ps1 export -IncludeCredentials
.\scripts\N8N-Workflows.ps1 import -IncludeCredentials
```

> Tips: kör `export` direkt efter att du sparat ändringar i n8n:s UI så att git-versionen alltid är uppdaterad. Var försiktig med hemligheter om du väljer att inkludera credentials i repo:t.

## Open WebUI Config Dumps

```powershell
# Spara nuvarande Open WebUI-databas som SQL-dump (för verktyg, presets m.m.)
./scripts/OpenWebUI-Config.ps1 export

# Återställ UI:t från senast exporterade dumpen
./scripts/OpenWebUI-Config.ps1 import
```

> Importen skriver över `/app/backend/data/app.db`. Stoppa gärna containern eller
se till att inga användare är aktiva för att undvika låsningar.
