# Capabilities — Outcome-Oriented View

## Today
- **Reasoning** (SV/EN) with local models via LiteLLM.
- **Research**: SearxNG → webfetch extraction → LiteLLM summary (sources included).
- **Actions groundwork**: n8n service online with persisted storage and health checks (awaiting workflows).

## Next
- **Actions Skeleton** (n8n):
  - Import and activate `agent_echo` webhook workflow.
  - Document POST contract in Open WebUI action preset.
  - Begin versioning exports (`n8n export --all`) under `flows/` directory.

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
