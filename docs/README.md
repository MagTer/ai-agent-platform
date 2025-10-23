# Documentation Index

> Start here when you load the project as an AI assistant (Codex).
>
> Use this index to understand the problem space, delivery approach, and
> the current milestone goals before making changes.

## Orientation
- [Project Profile](./PROJECT_PROFILE.md) — persona, vision, non-functional constraints, and
  assistant behaviour requirements.
- [Delivery Model](./DELIVERY_MODEL.md) — iteration cadence, Definition of Done, and
  review checklist.
- [Roadmap](./ROADMAP.md) — milestone sequencing, MVP steps, and expected outcomes.
- [Capabilities](./CAPABILITIES.md) — what the platform can do today, next, and later.
- [Architecture](./ARCHITECTURE.md) — active services, data flow, and security guidance.
- [Operations](./OPERATIONS.md) — scripts, health checks, smoke tests, and maintenance.
- [Open WebUI Actions](./OPENWEBUI_ACTIONS.md) — verktygskonfiguration för n8n-webhooken.

## Working Notes for Codex
1. **Language:** default to Swedish (sv-SE) when writing user-facing text, but code and
   commits stay in English.
2. **Iterations:** target 2–3 hour MVP steps with verifiable exit criteria. Update docs
   and smoke tests whenever behaviour changes.
3. **Skepticism welcome:** if you see a faster/safer approach, note it inline in the docs
   or PR summary (e.g., alternative automation ideas, better defaults).
4. **Versioning:** treat compose files, configuration, and n8n workflows as code. Persist
   exports in the repo whenever feasible.
5. **Open WebUI:** konfigurera verktyg via UI men exportera alltid till
   `openwebui/export/app.db.sql` efter ändringar.

## How to Contribute a Change
1. Review the milestone and step you are addressing in [ROADMAP.md](./ROADMAP.md).
2. Confirm prerequisites in [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) and
   [ARCHITECTURE.md](./ARCHITECTURE.md).
3. Run the appropriate smoke tests from [OPERATIONS.md](./OPERATIONS.md) after making changes.
4. Document new behaviours or scripts before finishing the iteration.

## Future Enhancements (Documented)
- Dedicated runbooks for production-like hosting once ACA migration starts.
- Automated `n8n` export/import scripts to keep workflows under version control.
- Guidance for memory/RAG pipelines once Qdrant ingestion is available.
