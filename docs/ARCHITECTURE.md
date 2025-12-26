# Architecture

The AI Agent Platform follows a **3-layer "Universal Agent" architecture**, designed to separate protocol handling, orchestration, and core execution. This ensures that the agent can be accessed via multiple interfaces (OpenWebUI, Slack, CLI) while maintaining a consistent skill execution logic.

```mermaid
graph TD
    subgraph Interfaces
        A[Open WebUI] -->|HTTP/JSON| B(Interface Adapter)
        C[Slack / Discord] -->|Events| B
    end

    subgraph Orchestrator
        B --> D[Agent Service]
        D -->|Plan| E[Planner Agent]
        E -->|Delegate| F[Skill Delegate]
        F -->|Load .md| G[Skill Loader]
        F -->|Execute| H[Worker Agent]
    end

    subgraph Core Engine
        D -.->|Direct Answer| I[LLM Client]
        H --> J[Tools / Capabilities]
        H --> I
        J --> K[WebFetch]
        J --> L[Embedder]
    end
```

## Layers

1.  **Interface Layer (`src/interfaces`)**:
    *   Adapts external protocols to internal data structures.
    *   Handles authentication, request validation, and response formatting.
    *   Example: `src/interfaces/http/openwebui_adapter.py` converts OpenAI-compatible requests into internal `AgentRequest` objects.

2.  **Orchestrator Layer (`src/orchestrator` / `src/core/agents`)**:
    *   **Planner Agent**: The high-level reasoning engine that breaks down user requests into a JSON plan.
    *   **Skill Delegate**: A specialized tool (`consult_expert`) that instantiates "Worker Agents" for specific domains.
    *   **Skill Loader**: Scans and loads file-based capabilities from the `skills/` directory.

3.  **Core Engine (`src/core`)**:
    *   The execution runtime.
    *   Manages LLM interactions (via LiteLLM).
    *   Handles Tool calling, Memory retrieval (RAG), and State management.

## Skill System

Skills are defined as **Markdown files** with YAML Frontmatter, located in the `skills/` directory.

*   **Definition**: A skill wraps a prompt template, execution parameters, and **allowed tools**.
*   **Discovery**: The `SkillLoader` scans `skills/` at startup.
*   **Execution**: The `Planner Agent` delegates tasks to skills via the `consult_expert` tool. Each skill runs as an isolated Worker Agent loop.

For detailed skill format, see [SKILLS_FORMAT.md](SKILLS_FORMAT.md).