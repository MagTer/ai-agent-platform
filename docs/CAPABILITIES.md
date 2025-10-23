# Capabilities — Outcome-Oriented View

## Today
- **Reasoning** (SV/EN) with local models via LiteLLM.
- **Research**: SearxNG → webfetch extraction → LiteLLM summary (sources included).

## Next
- **Actions Skeleton** (n8n):
  - Webhook-driven “Single Wrapper”
  - Capability catalog to map `action` → workflow

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
