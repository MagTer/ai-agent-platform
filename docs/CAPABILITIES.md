# Capabilities — Outcome-Oriented View

## Today
- Reasoning (EN/SV) with local models via LiteLLM profiles.
- Research: SearxNG -> webfetch extraction -> LiteLLM summary (sources included).
- Actions: n8n `/webhook/agent` echo workflow acknowledges JSON with a timestamp.

See `capabilities/catalog.yaml` for the machine-readable catalog.

## Next
- Actions skeleton (n8n):
  - Document POST contract in Open WebUI action preset.
  - Automate export steps (script) to keep `flows/` up to date (done).

## Capability Catalog
- File: `capabilities/catalog.yaml`
- Content: machine-readable overview of available actions, their contract, and which Open WebUI tool to use.
- Status field marks whether a capability is `available`, `in-progress`, or `planned`.

## Planned Capabilities
- Homey:
  - `homey.device_onoff(name, state)`
  - `homey.trigger_flow(name, params)`
- Obsidian:
  - `obsidian.write_daily_note(path, text)` (e.g., `Daily Notes/2025-04-23.md`)
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
- RAG:
  - `search_notes(query)` + `answer_with_context(query)`

