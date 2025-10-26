# Roadmap — Milestones and Iterative Steps

## Milestone M0 — Baseline Stack (DONE/IN PROGRESS)
**Goal:** Local stack runs; Reasoning + Research usable  
**Exit criteria:**
- Stack up via scripts; health checks pass
- Research POST tool works end-to-end
- Models persist across restarts

**MVP Steps (2–3h each):**
- M0.1: Compose services + scripts (up/down/logs) ✅
- M0.2: LiteLLM routing + budgets (local default) ✅
- M0.3: SearxNG + webfetch (POST /research + caching + retries) ✅
- M0.4: Open WebUI presets (Reasoning/Research) + tool config ✅

## Milestone M1 — Actions Skeleton (n8n)
**Goal:** Action calls via n8n webhook; capability catalog present  
**Exit criteria:**
- n8n running; `/webhook/agent` active
- “Echo” action returns ack JSON
- Capability catalog checked in
- Docs updated with n8n smoke test + export/import guidance

**MVP Steps:**
- M1.1: Add n8n service + health + persisted data ✅
 - M1.2: Import and activate `agent_echo` workflow ✅
- M1.3: Open WebUI POST tool `n8n_action` (JSON body)
- M1.4: Capability catalog v0 (YAML/JSON) and mapping Function node

## Milestone M2 — First Real Capabilities
**Goal:** At least 2 real actions behind n8n  
**Exit criteria:**
- `github.create_branch` (repo, base, name)
- `ado.create_work_item` or `m365.create_calendar_event`

**MVP Steps:**
- M2.1: n8n credential setup & secret handling
- M2.2: GitHub create-branch flow + tests
- M2.3: ADO or M365 action + tests

## Milestone M3 — Memory & RAG (IN PROGRESS)
**Goal:** Qdrant-backed retrieval + web fusion for better answers  
**Status:** Embedder microservice (CPU) + retrieval i webfetch, ingestion CLI

**Exit criteria:**
- Retrieval toggles och MMR/dedup aktiva (DONE)
- Ingestion från URL och filer (PARTIAL: URL klar)
- RAG röktest i OPERATIONS (DONE)

**MVP Steps (next):**
- M3.1: Expand ingestion CLI (lokala filer: .md/.pdf/.txt) och payload-index
- M3.2: Retrieval debug UI i Open WebUI preset eller separata endpoints (DONE basic)
- M3.3: RUNBOOKS för Qdrant schema, backup/restore, och prestandatuning
