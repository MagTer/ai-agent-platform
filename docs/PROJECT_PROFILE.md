# Project Profile — AI Agent Platform

## Vision
A local, containerized agent platform that can do things (not just reason) with low cost, portability, and clear security practices.

## Persona & Response Style
- Role: Senior assistant and advisor for IT product ownership (security, operations, AI).
- Language: English only for documentation, code, and chat responses.
- Style: Concise first; expand on request. Mark uncertainty with (assumption).
- No background work: deliver everything in the reply.

## Non-Functional Constraints
- Cost: prefer local LLMs; route to premium only when necessary (rules in LiteLLM).
- Security: secrets in `compose/.env`, never committed; least-privilege credentials.
- Portability: Docker-first; optional Azure Container Apps later.
- Versioning: everything as code (compose, config, flows, scripts).
- Recoverability: reset scripts and persistence for models/data.

## Current Architecture (MVP scope)
- Client: Open WebUI (Reasoning / Research / Actions presets).
- LLM Gateway: LiteLLM -> Ollama (GPU)
  - Unified local model: `qwen2.5:14b-instruct-q4_K_M`
  - LiteLLM profiles: `local/qwen2.5-en` and `local/qwen2.5-sv`
  - Optional premium: OpenRouter (routing-only).
- Research: SearxNG + webfetch (FastAPI) -> summarize via LiteLLM.
- Vector DB: Qdrant.
- Actions orchestrator: n8n Single Wrapper (webhook) with persisted volume; workflows versioned via exports.

## Outcome-Oriented View
- Reasoning (EN/SV) with local models via profiles.
- Research: search -> extract -> summarize with sources.
- Actions: baseline echo on `/webhook/agent`; next add real capabilities (Homey, Obsidian, GitHub, ADO, M365/Gmail, CLI/FFmpeg, YouTube, etc.).

## Onboarding Checklist
1. Skim `docs/README.md` to confirm current milestone and guardrails.
2. Follow the style and language in this profile when writing docs.
3. Ensure your plan matches the MVP step in `docs/ROADMAP.md` before coding.
4. Document tests or backup strategies when changing services (e.g., n8n exports).

