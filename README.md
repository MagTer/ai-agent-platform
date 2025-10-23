# AI Agent Platform (Local, Containerized)

> **For AI assistants (Codex) — Start here.**  
> All project documentation lives under [`/docs`](./docs). This root README links the essentials so you can locate **vision, constraints, delivery model (2–3h steps), roadmap, capabilities, architecture, operations, and runbooks** in one click.

---

## Quick Start (Local)

```powershell
# 1) Copy environment and edit if needed
cp .env.template .env

# 2) Bring the stack up (PowerShell)
.\scripts\Stack-Up.ps1

# 3) Smoke tests
# - Webfetch health
irm http://localhost:8081/health
# - Actions orchestrator health
irm http://localhost:5678/healthz
# - Research end-to-end (POST JSON)
$b=@{ query="Summarize pros/cons of Qdrant (Swedish)"; k=2; lang="sv" } | ConvertTo-Json -Compress
irm http://localhost:8081/research -Method POST -ContentType 'application/json' -Body $b
