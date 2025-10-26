# Operations

## Start
```powershell
cp .env.template .env
.\scripts\Stack-Up.ps1
```

> Models: `Stack-Up.ps1` säkerställer att basmodellen `qwen2.5:14b-instruct-q4_K_M` finns i `ollama` och skapar automatiskt den svenska profilen `qwen2.5-sv` från `ollama/models/qwen2.5-sv.modelfile` om den saknas.

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

## Testa svensk modell via LiteLLM

```bash
curl -sS -X POST http://localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "local/qwen2.5-sv",
    "messages": [
      {"role":"user","content":"Skriv två meningar på svenska om hur denna modell används i plattformen."}
    ],
    "temperature": 0.4
  }'
```

PowerShell-variant:

```powershell
$body = @{ 
  model = 'local/qwen2.5-sv';
  messages = @(@{ role='user'; content='Skriv två meningar på svenska om hur denna modell används i plattformen.' });
  temperature = 0.4
} | ConvertTo-Json -Compress
irm http://localhost:4000/v1/chat/completions -Method POST -ContentType 'application/json' -Body $body
```

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

## Svensk Qwen-modell

- Modelfil: `ollama/models/qwen2.5-sv.modelfile` (binder in som `/modelfiles` i Ollama)
- Auto-init: `Stack-Up.ps1` skapar `qwen2.5-sv` med `ollama create` om den inte redan finns.
- Användning via LiteLLM: modellnamn `local/qwen2.5-sv`
- `webfetch` väljer `local/qwen2.5-sv` automatiskt när `lang` börjar med `sv`.

## Qdrant Backups & Restore

```powershell
# Skapa snapshot av Qdrants lagring (tgz i ./backups)
./scripts/Qdrant-Backup.ps1

# Återställ från en specifik backupfil
./scripts/Qdrant-Restore.ps1 -BackupFile .\backups\qdrant-YYYYMMDD-HHMMSS.tgz
```

> Backup/restore använder en temporär Alpine-container med `--volumes-from qdrant` för
> att läsa/skriva `/qdrant/storage`. Containern stoppas/startas automatiskt vid restore.
