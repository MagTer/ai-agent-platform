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

**MVP Steps:**
- M1.1: Add n8n service + health + persisted data ✅
- M1.2: Import and activate `agent_echo` workflow
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

## Milestone M3 — Memory & RAG
**Goal:** Qdrant-backed notes (Obsidian) search and RAG answers  
**Exit criteria:**
- Index Obsidian vault (incremental)
- Research + memory blended answer (with source citations)

**MVP Steps:**
- M3.1: Ingestion job to Qdrant
- M3.2: Simple retriever → prompt augment in webfetch or LiteLLM function
- M3.3: RUNBOOKS and smoke tests
