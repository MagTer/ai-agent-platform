# AI Agent Platform Overview

The AI Agent Platform is a self-hosted research and automation environment designed for
deterministic local operation. The platform replaces the legacy n8n orchestrator with a
Python-based agent service built on FastAPI and LiteLLM. Docker Compose remains the
orchestration layer, ensuring repeatable, idempotent deployments.

```
+-------------------+       +-------------------+       +-------------------+
|   Open WebUI      | <-->  |  Agent (FastAPI)  | <-->  |     LiteLLM       |
|  (Reasoning UI)   |       |  Tools + Memory   |       |  Gateway to LLMs  |
+-------------------+       +-------------------+       +-------------------+
                                 |            \
                                 |             \
                           +-------------+   +-------------+
                           |   Qdrant     |   |   SQLite    |
                           | Vector DB    |   | Conversation|
                           +-------------+   |   Metadata  |
                                              +-------------+
                                 |
                                 v
                           +-------------+
                           |  Webfetch   |
                           |  Retrieval  |
                           +-------------+
```

Key principles:

- **Python First**: The agent service, orchestration CLI, and developer workflow are all
  Python-based and managed with Poetry.
- **Composable Services**: Each component runs in its own container, enabling targeted
  upgrades and debugging.
- **Observability**: Health checks, Stack CLI status reporting, and deterministic logging
  simplify operations.
