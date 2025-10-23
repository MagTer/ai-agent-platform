# n8n Workflows

Denna katalog innehåller exporterade n8n-flöden för agentplattformen.

## Synka workflows

Använd PowerShell-skriptet `scripts/N8N-Workflows.ps1` för att både säkerhetskopiera och återställa flöden från din n8n-instans:

```powershell
# Exportera alla workflows från körande n8n-container till repo:t
.\scripts\N8N-Workflows.ps1 export

# Importera workflows från repo:t till en tom n8n-instans
.\scripts\N8N-Workflows.ps1 import

# Ta även med credentials (lagras i flows/credentials.json)
.\scripts\N8N-Workflows.ps1 export -IncludeCredentials
.\scripts\N8N-Workflows.ps1 import -IncludeCredentials
```

- `flows/workflows/` innehåller ett JSON-dokument per workflow (genereras automatiskt vid export).
- `flows/workflows.json` är en samling av alla workflows och uppdateras automatiskt av skriptet som referens.

> Ändra workflows direkt i n8n:s UI och kör sedan `export` för att versionshantera uppdateringarna i git.

### Hur scriptet fungerar

1. Säkerställer att den namngivna n8n-containern kör innan någon synk påbörjas.
2. `export` kör `n8n export:workflow` inuti containern, uppdaterar `flows/workflows.json` och återskapar en JSON-fil per workflow i `flows/workflows/` (rensar bort filer som inte längre finns i n8n).
3. `import` läser in filerna från `flows/workflows/` (eller `flows/workflows.json` som reserv) och matar dem till `n8n import:workflow` för att återställa flödena.
4. Flaggan `-IncludeCredentials` tar med `flows/credentials.json` vid både export och import.

## Workflow: Agent Echo

- Fil: `flows/workflows/agent-echo--1.json`
- Webhook: `POST /webhook/agent`
- Syfte: Kvitterar inkommande `agent.echo`-anrop och returnerar mottagna argument med tidsstämpel.
