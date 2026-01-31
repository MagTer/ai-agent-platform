# Tools

Tools extend the agent with deterministic capabilities. Each tool implements the
`Tool` interface and can be referenced from `config/tools.yaml` (for declarative
loading) or instantiated manually. The agent boot sequence loads this file via
`core.tools.loader.load_tool_registry` so any updates are picked up without
code changes.

## Concepts

- **Tool**: Async callable with metadata (`name`, `description`).
- **ToolRegistry**: In-memory registry used to resolve tools by name.
- **tools.yaml**: Declarative definition of tool instances. Example snippet:

```yaml
- name: web_fetch
  type: core.tools.web_fetch.WebFetchTool
  args:
    base_url: http://webfetch:8081
    include_html: false
    summary_max_chars: 1200
```

The actual loader handles missing files gracefully and supports aliasing tool
names in configuration. See `core.tools.loader` for implementation details.

## Metadata Protocol

Clients can influence tool usage through the request `metadata` object:

- `tools`: Optional allow-list (array of strings). If present, only tools in the
  list may execute.
- `tool_calls`: Array of tool invocations. Each item may be a string (`"web_fetch"`)
  or an object with `name` and `args` keys:

```json
{
  "prompt": "Summarise the latest blog post.",
  "metadata": {
    "tools": ["web_fetch"],
    "tool_calls": [
      {
        "name": "web_fetch",
        "args": {"url": "https://qdrant.tech/blog"}
      }
    ]
  }
}
```

Successful executions are appended to the prompt as system messages and echoed
in both the response `steps` trace and the `metadata.tool_results` array. The
OpenAI-compatible endpoint exposes the same structure via
`choices[0].message.metadata` so Open WebUI can display action traces. Failures
are logged and reported with `status: "error"` so callers can react
deterministically.

## Planning Orchestration

The agent asks the planner model (via LiteLLM) to plan the work needed to respond before
making the final completion call. The planner is provided with the up-to-date
tool inventory (`memory`/RAG via Qdrant/embedder, `web_fetch`, `ragproxy`, plus
any MCP-registered helpers) so every step that needs a helper can reference it
by name. The plan is streamed through the `steps` array (`type: "plan"`,
`type: "plan_step"`, etc.) and preserved inside `metadata.plan` for replay in the UI.

### Plan schema

Planner output must be a strict JSON object containing a `steps` array. Each step has:

- `id`: unique identifier for referencing updates.
- `label`: short human description.
- `executor`: `agent`, `litellm`, or `remote`.
- `action`: `memory`, `tool`, or `completion`.
- `tool`: optional tool name when action is `tool`.
- `args`: optional dictionary consumed by the step (queries, URLs, tooling flags).
- `description`: optional narrative summary.
- `provider`: optional override when delegating to a remote LLM.

The planner should choreograph memory lookups (`action: "memory"`), deterministic
helpers (matching the registered tool names exactly), and the final completion
(`action: "completion"`). If the work requires a more capable remote LLM, set
`executor` to `remote` and specify `args.model`. Tool names used in the plan must be
drawn from the available tool list provided in the prompt, and the JSON response
must contain only the keys described above with no extra commentary.

```json
{
  "description": "Plan for answering the user query.",
  "steps": [
    {
      "id": "memory-1",
      "label": "Retrieve context",
      "executor": "agent",
      "action": "memory",
      "args": {"query": "previous conversation about GPUs"}
    },
    {
      "id": "tool-1",
      "label": "Fetch live blog post",
      "executor": "agent",
      "action": "tool",
      "tool": "web_fetch",
      "args": {"url": "https://example.com/blog"}
    },
    {
      "id": "completion-1",
      "label": "Compose final reply",
      "executor": "litellm",
      "action": "completion"
    }
  ]
}
```

Every executed plan step is appended to the `steps` array with `status`
information (`in_progress`, `ok`, `error`, etc.) so Open WebUI can surface
granular activity. Tool outputs are copied into `metadata.tool_results` for
auditing, and metadata from the request is passed along so the planner can respect
whitelists or pre-flight calls.

### Registered tools

The planner sees every tool declared in `config/tools.yaml`. The default registry
exposes:

| Tool | Purpose |
|------|---------|
| `web_fetch` | Retrieve rendered web content and provide summarized context. |

Memory/RAG tooling (`qdrant`, `embedder`, `ragproxy`) is invoked via the
`memory` action or will surface via additional MCP helpers if they are registered.
Add new entries to `config/tools.yaml` (see `core.tools.loader`) when you want
the planner model to orchestrate more capabilities.

### Web Fetch contract

`WebFetchTool` calls the fetcher service `/fetch` alias, which wraps the
existing `/extract` logic. The service returns an object of the shape:

```json
{
  "item": {
    "url": "https://example.com",
    "ok": true,
    "text": "Plain-text extraction…",
    "html": "<html>…</html>"
  }
}
```

The tool converts this payload into a prompt-ready system message that includes
the target URL, a truncated text snippet, and (optionally) a raw HTML snippet if
`include_html` is enabled. This keeps the planner grounded while preventing the
model from hallucinating unseen context.

When memory is enabled, semantic recalls from Qdrant are injected ahead of the
tool output. The agent therefore blends historical knowledge and fresh web
context before the LLM call.

## Testing Tools

1. Unit tests should stub out HTTP requests using `respx` or `httpx.MockTransport`.
2. `src/agent/tests/test_tools.py` exercises loader behaviour and the service
   orchestration path for tool dispatch.
3. Integration tests can run inside Docker Compose with the `webfetch` service.
4. When adding new tools, document their configuration and constraints inside this file.

## Example Usage

```python
from core.tools.registry import ToolRegistry
from core.tools.web_fetch import WebFetchTool

registry = ToolRegistry([WebFetchTool(base_url="http://webfetch:8081")])
result = await registry.get("web_fetch").run("https://example.com")
```

## Integration Checks

`integration_checks.py` (in `scripts/`) walks the stack to ensure each tier responds, covering:

1. LiteLLM `/v1/chat/completions` proxy to OpenRouter.
2. Agent `/v1/agent` (verifying the plan contains a completion step).
3. Qdrant `/collections` to confirm the vector database is up.
4. Agent-invoked memory work (the plan contains a `memory` step).

Run it from the repo root with the stack running:

```bash
python -m poetry run python scripts/integration_checks.py
```

You can override the service URLs (`LITELLM_URL`, `AGENT_URL`, `QDRANT_URL`) when ports differ.

