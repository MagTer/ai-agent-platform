# Documentation Index

Use this index to understand the problem space, delivery approach, and the current milestone goals before making changes.

## Orientation
- [Project Profile](./PROJECT_PROFILE.md) - persona, vision, non-functional constraints, and assistant behavior requirements.
- [Delivery Model](./DELIVERY_MODEL.md) - iteration cadence, Definition of Done, and review checklist.
- [Roadmap](./ROADMAP.md) - milestone sequencing, MVP steps, and expected outcomes.
- [Capabilities](./CAPABILITIES.md) - what the platform can do today, next, and later.
- [Architecture](./ARCHITECTURE.md) - active services, data flow, and security guidance.
- [Operations](./OPERATIONS.md) - scripts, health checks, smoke tests, and maintenance.
- [Testing](./TESTING.md) - how to run and what is covered.
- [Open WebUI Actions](./OPENWEBUI_ACTIONS.md) - tool configuration for the n8n webhook.
- [Style](./STYLE.md) - docs encoding and punctuation rules.

## Working Notes for Codex
1. Language: English only for user-facing text, docs, and code.
2. Iterations: target 2-3 hour MVP steps with verifiable exit criteria. Update docs and smoke tests whenever behavior changes.
3. Skepticism welcome: if you see a faster/safer approach, note it in the docs or PR summary (e.g., alternative automation ideas, better defaults).
4. Versioning: treat compose files, configuration, and n8n workflows as code. Persist exports in the repo whenever feasible.
5. Open WebUI: configure tools via the UI but always export to `openwebui/export/app.db.sql` after changes.

## How to Contribute a Change
1. Review the milestone and step you are addressing in [ROADMAP.md](./ROADMAP.md).
2. Confirm prerequisites in [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) and [ARCHITECTURE.md](./ARCHITECTURE.md).
3. Run the appropriate smoke tests from [OPERATIONS.md](./OPERATIONS.md) after making changes.
4. Document new behaviors or scripts before finishing the iteration.

## Future Enhancements (Documented)
- Dedicated runbooks for production-like hosting once Azure Container Apps migration starts.
- Automated n8n export/import scripts to keep workflows under version control.
- Guidance for memory/RAG pipelines once Qdrant ingestion is available.

