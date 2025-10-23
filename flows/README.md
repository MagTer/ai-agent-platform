# n8n Workflows

Denna katalog innehåller exporterade n8n-flöden för agentplattformen.

## Workflow: Agent Echo
- Fil: `workflows.json`
- Webhook: `POST /webhook/agent`
- Syfte: Kvitterar inkommande `agent.echo`-anrop och returnerar mottagna argument med tidsstämpel.
- Import: `docker exec n8n n8n import:workflow --input /home/node/.n8n/import-workflows.json --separate`

> Uppdatera exporten via kommandona i `docs/OPERATIONS.md` efter att du ändrat workflow i UI:t.
