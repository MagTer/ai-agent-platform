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
- Actions (planned): **n8n Single Wrapper** + capability catalog.

## Outcome-Oriented View
- Reasoning (SV/EN) with local models.
- Research: search → extract → summarize with sources.
- Actions (next): call server-side capabilities (GitHub, ADO, M365/Gmail, CLI/FFmpeg, YouTube, Homey, etc.).
