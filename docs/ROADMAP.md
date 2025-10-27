# Roadmap — Milestones and Iterative Steps

## Milestone M0 — Baseline Stack (DONE/IN PROGRESS)
Goal: Local stack runs; Reasoning + Research usable

Exit criteria:
- Stack up via scripts; health checks pass
- Research POST tool works end-to-end
- Models persist across restarts

MVP Steps (2-3h each):
- M0.1: Compose services + scripts (up/down/logs) ✓
- M0.2: LiteLLM routing + budgets (local default) ✓
- M0.3: SearxNG + webfetch (POST /research + caching + retries) ✓
- M0.4: Open WebUI presets (Reasoning/Research) + tool config ✓

## Milestone M1 — Actions Skeleton (n8n)
Goal: Action calls via n8n webhook; capability catalog present

Exit criteria:
- n8n running; `/webhook/agent` active
- "Echo" action returns ack JSON
- Capability catalog checked in
- Docs updated with n8n smoke test + export/import guidance

MVP Steps:
- M1.1: Add n8n service + health + persisted data ✓
- M1.2: Import and activate `agent_echo` workflow ✓
- M1.3: Open WebUI POST tool `n8n_action` (JSON body)
- M1.4: Capability catalog v0 (YAML/JSON)

## Milestone M2 — First Real Capabilities
Goal: Two real actions behind n8n

Exit criteria:
- `homey.device_onoff(name, state)`
- `obsidian.write_daily_note(path, text)`

MVP Steps:
- M2.1: n8n credential setup & secret handling (document default strategy)
- M2.2: Homey device on/off + tests/smoke
- M2.3: Obsidian daily note write + tests/smoke

## Milestone M3 — Memory & RAG (IN PROGRESS)
Goal: Qdrant-backed retrieval + web fusion for better answers

Status: Embedder microservice (CPU) + retrieval in webfetch, ingestion CLI

Exit criteria:
- Retrieval toggles and MMR/dedup active (DONE)
- Ingestion from URL and files (PARTIAL: URL done)
- RAG smoketest in OPERATIONS (DONE)

MVP Steps (next):
- M3.1: Expand ingestion CLI (local files: .md/.pdf/.txt) and payload indexing
- M3.2: Retrieval debug endpoints (DONE basic)
- M3.3: Runbooks for Qdrant schema, backup/restore, and tuning

## Backlog / Technical Notes
- n8n import overwrite flag: add `-OverwriteAll` option to `N8N-Workflows.ps1` to replace existing workflows (investigate CLI support and safety).
- Bind mount migration: design `.data/<service>` structure (gitignored) and a safe migration plan per service (ollama, qdrant, n8n). Avoid data loss; document rollback.
- Docs alignment: English-only, ASCII-safe punctuation. Keep `docs/STYLE.md` as guardrail.
- Security hardening (future): add `cap_drop`, `read_only`, and `no-new-privileges` per service where feasible.
