# Operations

## Start
```powershell
cp .env.template .env
.\scripts\Stack-Up.ps1
```

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
```

## Research Smoke Test
```powershell
$b=@{ query="RAG i kundsupport"; k=2; lang="sv" } | ConvertTo-Json -Compress
irm http://localhost:8081/research -Method POST -ContentType 'application/json' -Body $b
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
