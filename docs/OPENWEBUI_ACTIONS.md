# Open WebUI — Åtgärdsverktyg mot n8n

Detta dokument beskriver hur Open WebUI ska konfigureras för att kunna anropa
plattformens n8n-webhook. Målet är ett återanvändbart verktyg (`n8n_action`)
som skickar ett JSON-payload till `/webhook/agent` och presenterar svaret i
chatten.

## Förutsättningar
- Stacken kör enligt [compose/docker-compose.yml](../compose/docker-compose.yml).
- Kontot i Open WebUI har behörighet att skapa verktyg.
- n8n-workflowen **Agent Echo** är aktiv (se [`flows/workflows/`](../flows/workflows/)).

## Konfiguration i gränssnittet
1. Logga in i Open WebUI och öppna **Tools → Create Tool**.
2. Välj typen **REST** och ge verktyget namnet `n8n_action`.
3. Fyll i inställningarna enligt tabellen nedan:

| Fält | Värde |
| --- | --- |
| Description | `POST:a JSON till n8n:s agent-webhook` |
| Method | `POST` |
| URL | `http://n8n:5678/webhook/agent` |
| Headers | `Content-Type: application/json` |
| Body Template | <pre><code>{ "action": "agent.echo", "args": { "message": "&lt;skriv ditt meddelande&gt;" } }</code></pre> |
| Timeout | `15` sekunder |
| Authentication | `None` |

4. Spara verktyget och aktivera det för presetet **Actions** om efterfrågat.
5. Öppna en ny chatt, välj presetet **Actions** och kör kommandot
   `Run Tool → n8n_action`. Ändra `message` i body-templatet för att kontrollera
   att svaret ekas tillbaka.

> **Tips:** Kontraktet för `agent.echo` finns dokumenterat i
> [`capabilities/catalog.yaml`](../capabilities/catalog.yaml).

## Export till versionskontroll
När verktyget är skapat ska Open WebUI-databasen exporteras så att konfigurationen
kan checkas in:

```powershell
./scripts/OpenWebUI-Config.ps1 export
```

Detta kommando uppdaterar `openwebui/export/app.db.sql`. Filen ska committas så att
miljön kan återskapas utan manuella klick.

## Verifiering
1. Kör n8n-röktestet i [docs/OPERATIONS.md](./OPERATIONS.md).
2. Starta Open WebUI, välj presetet **Actions** och anropa `n8n_action`.
3. Bekräfta att svaret innehåller `"ok": true`, `"action": "agent.echo"` och
   att de skickade argumenten återkommer i `received.args`.
