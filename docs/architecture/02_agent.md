# Agent Service Architecture

The agent service is implemented as a FastAPI application with a layered design:

```
AgentRequest -> AgentService -> LiteLLMClient -> LiteLLM Gateway -> Ollama
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
2. **Dispatcher (Orchestrator)**: The request is intercepted by the `Dispatcher`.
   - If the message starts with a command (e.g., `/briefing`), it matches against loaded **Skills**.
   - If matched, the Skill is executed (rendering the prompt and invoking the agent).
   - If no command is found, it is routed as a **General Chat** request.
3. `AgentService` (for General Chat) fetches the latest conversation history from `StateStore`, unless
   the request provides an explicit message list (the OpenAI route does this), and
   retrieves semantic memories from `MemoryStore` (which converts the query via
   the embedder service before searching Qdrant).
4. Tool metadata is evaluated. Allowed tools execute (via the registry) and their
   results are injected as system messages for the upcoming completion.
5. LiteLLM is called with a composed message list. Errors are surfaced as 500 responses.
6. The new prompt is persisted to Qdrant and the incremental user/assistant
   messages are recorded in SQLite for observability.
7. `AgentResponse` is returned to the caller with the conversation ID and `tool_results`
   so clients can audit executed actions.

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
