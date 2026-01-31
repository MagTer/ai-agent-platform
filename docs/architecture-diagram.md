# Platform Architecture

This document provides a detailed view of the AI Agent Platform's architecture, showing how requests flow from Open WebUI through the system and how components interact.

## Component Overview

| Component | Layer | Purpose |
|-----------|-------|---------|
| **Open WebUI** | External | Chat frontend that sends OpenAI-compatible API requests |
| **OpenWebUI Adapter** | Interfaces | Translates HTTP requests to internal format, handles SSE streaming |
| **Dispatcher** | Orchestrator | Routes requests based on intent classification (chat vs agentic) |
| **AgentService** | Core | Coordinates planning, execution, and memory operations |
| **PlannerAgent** | Core/Agents | Generates execution plans from user requests |
| **PlanSupervisor** | Core/Agents | Validates plans before execution |
| **StepExecutor** | Core/Agents | Executes individual plan steps |
| **StepSupervisor** | Core/Agents | Validates step results, triggers re-planning if needed |
| **SkillExecutor** | Core/Skills | Executes skills with scoped tool access |
| **ToolRegistry** | Core/Tools | Manages available tools per context |
| **MemoryStore** | Core | Vector-based memory for context retrieval |
| **LiteLLMClient** | Core | Unified interface to multiple LLM providers |

---

## Request Flow Diagram

```mermaid
flowchart TB
    subgraph External ["External (Frontend)"]
        WebUI["Open WebUI<br/>(Chat Interface)"]
    end

    subgraph Interfaces ["Interfaces Layer"]
        Adapter["OpenWebUI Adapter<br/>/v1/chat/completions"]
        SSE["SSE Stream Generator"]
    end

    subgraph Orchestrator ["Orchestrator Layer"]
        Dispatcher["Dispatcher"]
        IntentClassifier["Intent Classifier"]
        SkillLoader["Skill Loader"]
    end

    subgraph Core ["Core Layer"]
        subgraph Service ["Agent Service"]
            AgentService["AgentService<br/>(Coordinator)"]
        end

        subgraph Agents ["Agent Pipeline"]
            Planner["Planner Agent<br/>(Plan Generation)"]
            PlanSupervisor["Plan Supervisor<br/>(Plan Validation)"]
            Executor["Step Executor<br/>(Plan Execution)"]
            StepSupervisor["Step Supervisor<br/>(Result Validation)"]
        end

        subgraph Tools ["Tools & Skills"]
            Registry["Tool Registry"]
            SkillExec["Skill Executor<br/>(scoped tools)"]
            NativeTools["Native Tools<br/>(web_fetch, search, etc.)"]
            MCPTools["MCP Tools<br/>(OAuth-enabled)"]
        end

        subgraph Memory ["Memory & Data"]
            MemoryStore["Memory Store<br/>(Qdrant)"]
            DB[("PostgreSQL<br/>(Conversations, Users)")]
        end

        subgraph LLM ["LLM Interface"]
            LiteLLM["LiteLLM Client"]
            Models["Models<br/>(Planner, Composer, etc.)"]
        end
    end

    subgraph Skills ["Skills (Markdown Files)"]
        Researcher["researcher.md"]
        BacklogManager["backlog_manager.md"]
        OtherSkills["Other Skills..."]
    end

    %% Main Flow
    WebUI -->|"POST /v1/chat/completions"| Adapter
    Adapter -->|"Extract context_id"| Adapter
    Adapter -->|"Create AgentService"| AgentService
    Adapter -->|"stream_message()"| Dispatcher

    Dispatcher -->|"Classify intent"| IntentClassifier
    IntentClassifier -->|"CHAT | AGENTIC"| Dispatcher

    Dispatcher -->|"CHAT: Direct LLM"| LiteLLM
    Dispatcher -->|"AGENTIC: Execute"| AgentService

    AgentService -->|"Generate plan"| Planner
    Planner -->|"Structured JSON Plan"| PlanSupervisor
    PlanSupervisor -->|"Validated Plan"| AgentService

    AgentService -->|"Execute steps"| Executor
    Executor -->|"Step results"| StepSupervisor
    StepSupervisor -->|"OK | ADJUST"| AgentService

    Executor -->|"Tool calls"| Registry
    Registry -->|"Get tool"| NativeTools
    Registry -->|"Get tool"| MCPTools
    Executor -->|"Skill steps"| SkillExec

    SkillExec -->|"Load skill"| SkillLoader
    SkillLoader -->|"Parse .md"| Researcher
    SkillLoader -->|"Parse .md"| BacklogManager
    SkillLoader -->|"Parse .md"| OtherSkills

    SkillExec -->|"Worker loop"| LiteLLM
    SkillExec -->|"Scoped tools"| NativeTools

    Executor -->|"Memory search"| MemoryStore
    AgentService -->|"Persist messages"| DB

    Planner -->|"Generate"| LiteLLM
    Executor -->|"Completion"| LiteLLM
    StepSupervisor -->|"Review"| LiteLLM

    LiteLLM -->|"API calls"| Models

    AgentService -->|"Stream events"| Dispatcher
    Dispatcher -->|"AgentChunks"| SSE
    SSE -->|"SSE events"| Adapter
    Adapter -->|"data: {...}"| WebUI
```

---

## Detailed Component Interactions

### 1. Request Entry Point

```mermaid
sequenceDiagram
    participant WebUI as Open WebUI
    participant Adapter as OpenWebUI Adapter
    participant Factory as ServiceFactory
    participant Service as AgentService
    participant Dispatcher

    WebUI->>Adapter: POST /v1/chat/completions
    Note over Adapter: Extract user from<br/>X-OpenWebUI-* headers

    Adapter->>Factory: get_or_create_context_id()
    Factory-->>Adapter: context_id (UUID)

    Adapter->>Factory: create_service(context_id)
    Note over Factory: Clone tool registry<br/>Load MCP tools<br/>Create MemoryStore
    Factory-->>Adapter: AgentService instance

    Adapter->>Dispatcher: stream_message(session_id, message)
    Dispatcher-->>Adapter: AsyncGenerator[AgentChunk]

    loop Stream Events
        Adapter->>WebUI: SSE: data: {"choices": [...]}
    end

    Adapter->>WebUI: data: [DONE]
```

### 2. Planning and Execution Pipeline

```mermaid
sequenceDiagram
    participant Service as AgentService
    participant Planner as PlannerAgent
    participant PlanSuper as PlanSupervisor
    participant Executor as StepExecutor
    participant StepSuper as StepSupervisor
    participant LLM as LiteLLM

    Service->>Planner: generate_stream(request, history, tools)
    Planner->>LLM: stream_chat(system + user)
    LLM-->>Planner: JSON plan tokens
    Planner-->>Service: Plan object

    Service->>PlanSuper: review(plan)
    Note over PlanSuper: Validate tool names<br/>Check skill existence
    PlanSuper-->>Service: Validated Plan

    loop For each step in plan
        Service->>Executor: run_stream(step, request)

        alt Tool Step
            Executor->>Executor: _run_tool_gen()
            Note over Executor: Execute tool
        else Skill Step
            Executor->>Executor: _run_skill_gen()
            Note over Executor: Execute skill with scoped tools
        else Completion Step
            Executor->>LLM: stream_chat(history)
            LLM-->>Executor: Completion tokens
        else Memory Step
            Executor->>Executor: search_memory()
        end

        Executor-->>Service: StepResult

        alt Not Completion Step
            Service->>StepSuper: review(step, result)
            StepSuper->>LLM: evaluate(step, output)
            LLM-->>StepSuper: {decision, reason}
            StepSuper-->>Service: "ok" | "adjust"

            alt decision == "adjust"
                Note over Service: Add feedback to history<br/>Trigger re-planning
            end
        end
    end
```

### 3. Skill Execution

```mermaid
sequenceDiagram
    participant Executor as StepExecutor
    participant SkillExec as SkillExecutor
    participant Registry as SkillRegistry
    participant LLM as LiteLLM
    participant Tools as Scoped Tools

    Executor->>SkillExec: execute(skill="researcher", args={...})

    SkillExec->>Registry: get("researcher")
    Registry-->>SkillExec: Skill (metadata, prompt, tools)

    SkillExec->>SkillExec: Filter tools to skill.tools only
    Note over SkillExec: SECURITY: Only tools<br/>declared in skill frontmatter

    loop Worker Loop (max_turns)
        SkillExec->>LLM: stream_chat(messages, tools=scoped_tools)

        alt No Tool Calls
            LLM-->>SkillExec: Final answer content
            Note over SkillExec: Return result
        else Tool Calls
            LLM-->>SkillExec: tool_calls array
            loop For each tool_call
                SkillExec->>Tools: tool.run(**args)
                Tools-->>SkillExec: Tool output
                Note over SkillExec: Add to messages
            end
        end
    end

    SkillExec-->>Executor: {output: ..., source_count: N}
```

---

## Layer Dependency Rules

```mermaid
graph TB
    subgraph Legend
        L1[Layer can import]
        L2[Cannot import]
    end

    subgraph Architecture
        Interfaces["interfaces/<br/>(HTTP, CLI)"]
        Orchestrator["orchestrator/<br/>(Dispatcher, SkillLoader)"]
        Modules["modules/<br/>(RAG, Indexer, Embedder)"]
        Core["core/<br/>(DB, Models, Config)"]
    end

    Interfaces -->|"Can import"| Orchestrator
    Interfaces -->|"Can import"| Modules
    Interfaces -->|"Can import"| Core

    Orchestrator -->|"Can import"| Modules
    Orchestrator -->|"Can import"| Core

    Modules -->|"Can import"| Core

    Core -.->|"NEVER imports"| Modules
    Core -.->|"NEVER imports"| Orchestrator
    Core -.->|"NEVER imports"| Interfaces

    Modules -.->|"Cannot import"| Modules

    style Core fill:#e1f5fe
    style Modules fill:#fff3e0
    style Orchestrator fill:#f3e5f5
    style Interfaces fill:#e8f5e9
```

---

## Multi-Tenant Context Isolation

```mermaid
flowchart LR
    subgraph Request ["Incoming Request"]
        Headers["X-OpenWebUI-User-Email<br/>X-OpenWebUI-User-Name"]
        ChatID["chat_id"]
    end

    subgraph Factory ["ServiceFactory"]
        Clone["Clone base registry"]
        Filter["Filter by permissions"]
        MCP["Load MCP tools"]
        Memory["Create MemoryStore"]
    end

    subgraph Isolated ["Context-Isolated Service"]
        Service["AgentService"]
        Registry["Tool Registry<br/>(filtered)"]
        Store["MemoryStore<br/>(context_id scoped)"]
        Tokens["OAuth Tokens<br/>(per-context)"]
    end

    Headers --> |"Auto-provision user"| Factory
    ChatID --> |"Resolve context_id"| Factory

    Factory --> Clone
    Clone --> Filter
    Filter --> MCP
    MCP --> Memory

    Memory --> Service
    Service --> Registry
    Service --> Store
    Service --> Tokens
```

---

## Adaptive Execution Loop

The system supports re-planning when step execution fails validation:

```mermaid
stateDiagram-v2
    [*] --> Planning

    Planning --> PlanReview
    PlanReview --> Execution: Plan Valid

    state Execution {
        [*] --> ExecuteStep
        ExecuteStep --> StepReview
        StepReview --> ExecuteStep: OK + More Steps
        StepReview --> [*]: OK + Complete
        StepReview --> ReplanNeeded: ADJUST
    }

    ReplanNeeded --> Planning: replans_remaining > 0
    ReplanNeeded --> ForceContinue: replans_remaining == 0

    ForceContinue --> Execution

    Execution --> FinalAnswer: Completion Step OK
    FinalAnswer --> [*]
```

---

## Key Event Types

The system uses typed events for streaming responses:

| Event Type | Description | When Emitted |
|------------|-------------|--------------|
| `thinking` | Internal reasoning/status | Plan generation, step transitions |
| `plan` | Execution plan created | After PlannerAgent completes |
| `step_start` | Step execution beginning | Before each step runs |
| `tool_start` | Tool call beginning | When tool is invoked |
| `tool_output` | Tool execution result | After tool completes |
| `skill_activity` | Skill worker activity | During skill execution (search, fetch) |
| `content` | Response content | Final answer tokens |
| `error` | Error occurred | On failures |
| `history_snapshot` | Conversation state | After completion |

---

## File Locations

| Component | File Path |
|-----------|-----------|
| OpenWebUI Adapter | `services/agent/src/interfaces/http/openwebui_adapter.py` |
| Dispatcher | `services/agent/src/orchestrator/dispatcher.py` |
| AgentService | `services/agent/src/core/core/service.py` |
| ServiceFactory | `services/agent/src/core/core/service_factory.py` |
| PlannerAgent | `services/agent/src/core/agents/planner.py` |
| StepExecutor | `services/agent/src/core/agents/executor.py` |
| StepSupervisor | `services/agent/src/core/agents/supervisor_step.py` |
| PlanSupervisor | `services/agent/src/core/agents/supervisor_plan.py` |
| SkillRegistry | `services/agent/src/core/skills/registry.py` |
| SkillExecutor | `services/agent/src/core/skills/executor.py` |
| ToolRegistry | `services/agent/src/core/tools/registry.py` |
| SkillLoader | `services/agent/src/orchestrator/skill_loader.py` |
| LiteLLMClient | `services/agent/src/core/core/litellm_client.py` |
| MemoryStore | `services/agent/src/core/core/memory.py` |

---

## Summary

The AI Agent Platform follows a clean 4-layer architecture:

1. **Interfaces** - Protocol adapters (HTTP/SSE for Open WebUI)
2. **Orchestrator** - Request routing and skill management
3. **Core** - Execution engine with planning, supervision, and tools
4. **Modules** - Isolated feature modules (RAG, Indexer, Embedder)

Requests flow from Open WebUI through the adapter, get classified by intent, and either:
- **CHAT**: Direct LLM response
- **AGENTIC**: Full planning/execution pipeline with tool use and adaptive re-planning

The system provides multi-tenant isolation through context-scoped services, ensuring each user's data and tools remain separate.
