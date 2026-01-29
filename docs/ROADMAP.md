# Roadmap — Milestones and Iterative Steps

The roadmap reflects the transition to a Python-centric agent stack. Each milestone is scoped to a handful of verifiable deliverables that can be checked in CI and via the stack CLI.

## Milestone M0 — Python Core (DONE)
Goal: Replace PowerShell/n8n orchestration with FastAPI + Typer.

Exit criteria:
- `python -m stack up/down/status` operate idempotently.
- `/v1/agent` handles basic prompts and persists conversation metadata.
- Docker Compose stack starts agent, LiteLLM, Qdrant, WebUI, and SearXNG.

## Milestone M1 — Tooling Foundation (IN PROGRESS)
Goal: Expand the agent-native tool layer and document usage.

Exit criteria:
- `config/tools.yaml` contains at least two tools with automated tests in `src/agent/tests/`.
- Open WebUI Actions preset calls the agent API using tool metadata.
- Capability catalog updated with available/planned entries.

MVP Steps:
- M1.1: Add filesystem summarisation tool with unit tests.
- M1.2: Document tool development workflow (`docs/architecture/03_tools.md`).
- M1.3: Extend smoke tests in `docs/OPERATIONS.md` to cover tool-triggered flows.

## Milestone M2 — Memory & Retrieval Enhancements
Goal: Enrich Qdrant ingestion and surface retrieval quality metrics.

Exit criteria:
- Ingestion CLI ingests Markdown/PDF assets and records provenance.
- Agent responses include cited context when memory is used.
- Qdrant backup/restore documented with tested scripts.

MVP Steps:
- M2.1: Implement ingestion pipeline in `src/agent/tools/` or `scripts/`.
- M2.2: Add pytest coverage for retrieval ranking.
- M2.3: Update `docs/architecture/02_agent.md` with memory flow diagrams.

## Milestone M3 — Action Integrations
Goal: Connect external systems (calendar, repositories, automation) via agent tools.

Exit criteria:
- At least two external integrations exposed through `/v1/agent` metadata.
- Secrets stored via `.env` with documented rotation steps.
- CI includes contract tests or mocks for each integration.

MVP Steps:
- M3.1: Calendar provider tool with mocked tests.
- M3.2: Git repository interaction tool with dry-run mode.
- M3.3: Update capability catalog and Open WebUI documentation for new tools.

## Backlog / Technical Notes
- Hardening: apply Docker security flags (`no-new-privileges`, `read_only`) where compatible.
- Telemetry: capture structured JSON logs for agent requests and tool usage.
- Deployment: evaluate packaging for Azure Container Apps once local workflows stabilise.
