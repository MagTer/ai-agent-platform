# Capabilities — Outcome-Oriented View

## Today
- **Reasoning** (SV/EN) with local models via LiteLLM.
- **Research**: SearxNG → webfetch extraction → LiteLLM summary (sources included).
- **Actions**: n8n `/webhook/agent` echo workflow som kvitterar JSON med tidsstämpel.

> Detaljerad kontraktsbeskrivning finns i [`capabilities/catalog.yaml`](../capabilities/catalog.yaml).

## Next
- **Actions Skeleton** (n8n):
  - Document POST contract in Open WebUI action preset.
  - Automatisera exportsteg (skript) för att hålla `flows/` uppdaterad.

## Capability Catalog
- Fil: [`capabilities/catalog.yaml`](../capabilities/catalog.yaml)
- Innehåll: Maskinläsbar översikt över tillgängliga åtgärder,
  deras kontrakt samt vilket Open WebUI-verktyg som ska användas.
- Statusfältet markerar om en förmåga är aktiv (`available`), under arbete
  (`in-progress`) eller planerad (`planned`).

## Planned Capabilities
- GitHub:
  - `create_branch(repo, base, name)`
  - `open_pr(repo, branch, title, body)`
- Azure DevOps:
  - `create_work_item(project, type, title, description)`
- Microsoft 365 / Gmail:
  - `send_mail(to, subject, body)`
  - `create_event(calendar, when, duration, title)`
- CLI / FFmpeg:
  - `transcode(input, profile)`
- YouTube:
  - `transcript(url)`; `search(query)`
- Homey:
  - `trigger_flow(name, params)`
- RAG:
  - `search_notes(query)` + `answer_with_context(query)`
