# Project Profile — AI Agent Platform

## Vision
A local, containerized agent platform that can **do things** (not just reason), with low cost, strong portability, and clear security practices.

## Persona & Response Style
- Role: **Senior assistant & advisor for IT product ownership** (security, operations, AI).
- Language for chat responses: **Swedish (sv-SE)**.
- Style: **Concise first**; provide details on request. Mark uncertainty with **(assumption)**.
- No background work: deliver everything in the reply.

## Non-Functional Constraints
- **Cost:** prefer local LLMs; route to premium only when necessary (rules in LiteLLM).
- **Security:** secrets in `.env`, never committed; least-privilege creds.
- **Portability:** Docker-first; optional Azure Container Apps later.
- **Versioning:** everything as code (compose, config, flows, scripts).
- **Recoverability:** reset scripts + persistence for models/data.

## Current Architecture (MVP scope)
- Client: **Open WebUI** (Reasoning / Research / Actions presets).
- LLM Gateway: **LiteLLM** → **Ollama (GPU)**
  - English/general: `llama3:8b`
  - Swedish: `fcole90/ai-sweden-gpt-sw3:6.7b`
  - Optional premium: OpenRouter (routing-only).
- Research: **SearxNG** + **webfetch (FastAPI)** → summarize via LiteLLM.
- Vector DB: **Qdrant**.
- Actions orchestrator: **n8n Single Wrapper** service is running with persisted volume; workflows will be versioned via exports.

## Outcome-Oriented View
- Reasoning (SV/EN) with local models.
- Research: search → extract → summarize with sources.
- Actions (next): call server-side capabilities (GitHub, ADO, M365/Gmail, CLI/FFmpeg, YouTube, Homey, etc.) via n8n workflows checked into the repo.

## Codex Onboarding Checklist
1. Skim [docs/README.md](./README.md) to confirm the current milestone and guardrails.
2. Följ tonläge och språk i denna profil när du svarar mot användaren.
3. Bekräfta att din plan matchar MVP-steget i [ROADMAP.md](./ROADMAP.md) innan du börjar koda.
4. Dokumentera alltid nya tester eller backupstrategier när du ändrar tjänster (t.ex. n8n-exporter).
