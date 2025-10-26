# Operations

## Start
```powershell
cp .env.template .env
.\scripts\Stack-Up.ps1
```

> Models: `Stack-Up.ps1` säkerställer att basmodellen `qwen2.5:14b-instruct-q4_K_M` finns i `ollama`. Svensk profil tillhandahålls via LiteLLM‑aliaset `local/qwen2.5-sv` utan att skapa en separat Ollama‑modell.

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

## Test English model via LiteLLM

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

PowerShell variant:

```powershell
$body = @{ 
  model = 'local/qwen2.5-en';
  messages = @(@{ role='user'; content='Write two concise sentences in English about how this model is used in the platform.' });
  temperature = 0.35
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

### Beständiga inloggningar i Open WebUI

För att slippa logga in efter varje omstart, använd en stabil signeringsnyckel:

- Sätt `OPENWEBUI_SECRET` i `.env` (en lång slumpmässig sträng).
- Compose injicerar den som `SECRET_KEY` och `WEBUI_JWT_SECRET` till containern.
- Cookies förblir giltiga över container‑omstarter så länge hemligheten är densamma.

TTL för tokens (valfritt och versionsberoende):
- I vissa 0.6‑builds kan anpassad TTL ge felet "Invalid duration string". Om det händer – lämna TTL odefinierad och använd standard.
- När stöd finns: sätt TTL via miljövariabler enligt projektets dokumentation för din version och verifiera i JWT `exp`.

## Svensk Qwen-profil via LiteLLM

- Alias: `local/qwen2.5-sv` i `litellm/config.yaml`
- Mappas till samma Ollama‑modell: `ollama/qwen2.5:14b-instruct-q4_K_M` (ingen VRAM‑reload)
- Justeringar (t.ex. temperatur, systemprompt) hanteras i LiteLLM‑aliaset
- `webfetch` väljer `local/qwen2.5-sv` automatiskt när `lang` börjar med `sv`

## Qdrant Backups & Restore

```powershell
# Skapa snapshot av Qdrants lagring (tgz i ./backups)
./scripts/Qdrant-Backup.ps1

# Återställ från en specifik backupfil
./scripts/Qdrant-Restore.ps1 -BackupFile .\backups\qdrant-YYYYMMDD-HHMMSS.tgz
```

> Backup/restore använder en temporär Alpine-container med `--volumes-from qdrant` för
> att läsa/skriva `/qdrant/storage`. Containern stoppas/startas automatiskt vid restore.
