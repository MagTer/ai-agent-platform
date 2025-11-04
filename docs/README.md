# Documentation Index

Use this index as the single entry point into the refactored Python-based stack. Each section links to the primary reference that should be updated alongside any code change.

## Orientation
- [Project Profile](./PROJECT_PROFILE.md) – persona, product vision, and non-functional constraints that apply to every change.
- [Delivery Model](./DELIVERY_MODEL.md) – iteration cadence, Definition of Done, and review checklist tuned for the Python agent workflow.
- [Contributing Guide](./contributing.md) – Codex-specific coding rules, required local checks, and dependency/documentation hygiene.
- [Roadmap](./ROADMAP.md) – milestone sequencing for the FastAPI agent, stack CLI, and memory features.
- [Capabilities](./CAPABILITIES.md) – current and planned behaviours exposed by the agent API and tool layer.
- [Architecture Overview](./architecture/README.md) – high-level service map plus pointers into the detailed `docs/architecture/` set.
- [Operations](./OPERATIONS.md) – Typer-based stack commands, health checks, smoke tests, and maintenance procedures.
- [Testing](./TESTING.md) – how to run linting and tests with Poetry and pytest, including coverage expectations.
- [Open WebUI Integration](./OPENWEBUI_ACTIONS.md) – configuring the UI to call the FastAPI agent and expose tools.
- [Style](./STYLE.md) – documentation formatting guardrails (ASCII punctuation, wrapping, tone).

## Working Notes for Codex
1. Language: English only for user-facing text, docs, and code.
2. Idempotence: stack commands and scripts must tolerate repeated runs (`python -m stack up/down/status`).
3. Synchronise docs: update the relevant file in `docs/architecture/` and the corresponding top-level doc when behaviour changes.
4. Prefer local execution paths; document any premium or remote dependencies explicitly.
5. Treat Docker Compose, environment files, and the agent configuration as code – commit generated outputs when feasible.

## Contributing Workflow
1. Identify the roadmap item you are advancing in [ROADMAP.md](./ROADMAP.md).
2. Confirm constraints in [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) and architectural intent via [architecture/README.md](./architecture/README.md).
3. Implement changes with Poetry-managed tooling (`poetry run ...`), keeping the stack CLI commands idempotent.
4. Run linting, tests, and any relevant smoke tests documented in [OPERATIONS.md](./OPERATIONS.md).
5. Update documentation to reflect the behaviour change before opening a PR.

## Future Enhancements to Capture
- Production runbooks for deploying the FastAPI agent and stack CLI outside of local Docker.
- Expanded capability catalog entries for tool-based actions and memory-aware workflows.
- Operational playbooks for Qdrant backup/restore and LiteLLM routing policies.
