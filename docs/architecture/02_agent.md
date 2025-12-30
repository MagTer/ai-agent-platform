# Agent Service Architecture

The agent service is implemented as a FastAPI application with a layered design:

```
AgentRequest -> AgentService -> LiteLLMClient -> LiteLLM Gateway -> OpenRouter
                    |                 |
                    |                 +-> Error handling and retries
                    v
             MemoryStore (Qdrant)
                    |
                    v
              StateStore (SQLite)
```

## Modules

- `agent.core.config`: Pydantic settings with `.env` integration.
- `agent.core.models`: Pydantic models shared by the API and internal components.
- `agent.core.litellm_client`: Async LiteLLM wrapper with structured errors.
- `agent.core.memory`: Qdrant client that defers to the embedder service for vectorisation (fallbacking to a deterministic embedding for local tests).
- `agent.core.state`: SQLite metadata persistence.
- `agent.core.service`: High-level orchestration combining LiteLLM, memory, and state.
- `agent.core.app`: FastAPI factory and REST endpoints.

The embedder endpoint (`AGENT_EMBEDDER_URL`) is used whenever the memory store inserts or searches vectors, so Qdrant remains aligned with the retrieval stack. If the embedder cannot be reached during development, the agent falls back to the deterministic ASCII embedding to keep tests and local loops operational.

## Request Lifecycle

1. FastAPI receives either a JSON-native `AgentRequest` at `/v1/agent` or an
   OpenAI-compatible payload at `/v1/chat/completions` (used by Open WebUI).
2. **AgentService** initializes the request context.
3. **Planner Agent (Orchestrator)**: The `PlannerAgent` analyzes the user input and generates a JSON execution plan.
    - It does **not** execute tasks directly.
    - It delegates domain-specific work using the `consult_expert` tool.
4. **Adaptive Execution Loop** (with re-planning):
    - The `AgentService` iterates through the plan steps within an outer re-plan loop.
    - **Step Execution**: Each step is executed by `StepExecutorAgent`.
    - **Step Supervision**: After each step, `StepSupervisorAgent` uses an LLM to evaluate if the output satisfies the step's intent.
        - Detects: empty results, hidden errors, intent mismatches, hallucinations.
        - Returns `decision` ("ok" or "adjust") with `reason`.
    - **Re-planning**: If `decision == "adjust"`:
        - Feedback is injected into the conversation history.
        - Execution halts and the Planner generates a new plan.
        - Safety limit: max 3 re-plans to prevent infinite loops.
    - **Skill Delegation**: If a step calls `consult_expert`, the `SkillDelegateTool` is invoked.
5. **Completion**:
   - The final step of the plan is typically a `completion` action, where the LLM synthesizes the results into a natural language response.
6. The `AgentResponse` is returned to the caller, including the full `steps` trace for UI visualization.

## Orchestrating with a planning agent

The agent is the user’s “speaking partner” in Open WebUI. Before it makes any calls,
LiteLLM (Llama 3.1 8B via the LiteLLM gateway) first ingests the question together with the
catalog of available tools (RAG/Embedder, WebFetch, other MCP-registered helpers) and
produces a lightweight, structured plan. The plan lists the steps that have to execute
before returning a response, and the client can stream those steps as they happen so the
user always sees ongoing progress.

Each planned step may run locally inside `AgentService` (memory lookups, tool runs, state
updates) or be forwarded back to LiteLLM with the subset of MCP tools required for the
LLM to orchestrate extra work. If the planner decides the final answer should go through a
larger remote LLM, that call is scheduled as the last step and annotated accordingly.

Every execution and heuristic decision is logged via the `steps` trace and duplicated into the
`metadata` blob (`metadata.plan`, `metadata.tool_results`). This keeps the orchestration transparent,
enables streaming updates to the Open WebUI client, and makes it easy to inspect why a particular
model or tool was chosen.

All internal reasoning runs on the shared English Llama 3.1 8B model. Swedish input is translated to English before the plan is executed, and the final response can be routed via a translation tool or OpenRouter so the end user still receives Swedish text without the agent having to host a second LLM.
inspect why a particular tool or LLM was chosen.

## Response Contract

Every API call returns a structured `AgentResponse` (or its OpenAI-compatible
variant) with three key sections:

- `response`: the assistant's natural-language answer.
- `steps`: an ordered list of orchestration events (memory retrieval, tool
  invocations, LiteLLM completion) describing how the answer was produced.
- `metadata`: caller-supplied metadata enriched with execution details such as
  `tool_results`.

Open WebUI consumes the same structure via `/v1/chat/completions`; the steps
payload is exposed both at the top level and inside each choice's message
metadata so the UI can render tool traces alongside the final answer.

## Tooling

Tools are plain Python classes implementing the `Tool` interface. They are registered via
`ToolRegistry` and can be injected into the service layer. Example: `WebFetchTool` wraps the
internal fetcher microservice to enrich prompts with external context.
