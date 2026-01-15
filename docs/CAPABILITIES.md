# Capabilities — Outcome-Oriented View

## Today
- **Conversational agent API**: `/v1/agent` accepts a prompt, optional `conversation_id`, and metadata to drive tool usage. `/v1/chat/completions` exposes the same orchestrated flow via the OpenAI schema so Open WebUI can consume structured responses with embedded metadata.
- **Local reasoning**: LiteLLM proxies to OpenRouter-hosted models (Llama 3.3, DeepSeek).
- **Web research tool**: the `web_fetch` tool calls the internal WebFetch module to blend search and memory before LiteLLM completion.
- **Semantic memory**: Qdrant stores embeddings for retrieval-augmented prompts; SQLite tracks conversation metadata for continuity.

See `config/tools.yaml` for the runtime tool registry and `docs/architecture/03_tools.md` for testing guidance.

## Next
- **Expanded tool catalog**: add filesystem, calendar, and code execution utilities implemented directly in `src/agent/tools/` with declarative entries in `config/tools.yaml`.
- **Memory enrichment**: scheduled ingestion workflows for documentation, enabling richer answers without manual prompting.
- **Observation hooks**: push interaction metrics (latency, tokens, tool usage) into structured logs for later analytics.

## Capability Catalog
The previous n8n-focused catalog is being replaced with agent-native definitions. Track new entries under `capabilities/catalog.yaml` using the following schema:

- `id`: globally unique capability identifier (e.g., `agent.web_research`).
- `owner`: `agent` for FastAPI-native features or the service name for external dependencies.
- `entrypoint`: HTTP method + URL exposed by the platform.
- `contract`: JSON schema for requests/responses.
- `verification`: reproducible smoke test command (`curl`, `pytest`, or stack CLI invocation).

Update the catalog whenever a capability graduates from planned to available and mirror the status in this document.

## Implemented Capabilities

### Team-Aware Azure DevOps Management

The platform understands Azure DevOps team structure for intelligent backlog management:

**Skills:**
- `backlog_manager` - List, search, analyze work items by team
- `requirements_drafter` - Create work items with team validation and suggestions

**Team Discovery:**
```
User: "List all configured teams"
→ backlog_manager uses azure_devops(action="get_teams")
→ Returns teams with area paths and default types
```

**Team-Specific Queries:**
```
User: "What's the Platform team working on?"
→ backlog_manager lists Platform's active work items

User: "Create a security incident for the Security team"
→ requirements_drafter validates team, creates with correct area/tags
```

**Configuration:**
Teams are defined in `services/agent/config/ado_mappings.yaml`. See [Team Structure Guide](AZURE_DEVOPS_TEAMS.md) for details.

**Key Features:**
- Automatic team validation with smart suggestions (Levenshtein distance)
- Resolved configuration preview before work item creation
- Cross-team workload comparison and analytics
- Keyword-based team suggestions (security, infra, platform)

---

## Planned Capabilities
- `agent.web_research` – orchestrated retrieval across Qdrant memory and live web data.
- `agent.file_summarise` – ingest local Markdown/PDF documents and summarise with citations.
- `agent.calendar_event` – create calendar entries through a provider-agnostic tool.
- `agent.repo_change` – draft pull requests using repository context and Git tooling.
