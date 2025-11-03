# Tools

Tools extend the agent with deterministic capabilities. Each tool implements the
`Tool` interface and can be referenced from `config/tools.yaml` (for declarative
loading) or instantiated manually. The agent boot sequence loads this file via
`agent.tools.loader.load_tool_registry` so any updates are picked up without
code changes.

## Concepts

- **Tool**: Async callable with metadata (`name`, `description`).
- **ToolRegistry**: In-memory registry used to resolve tools by name.
- **tools.yaml**: Declarative definition of tool instances. Example snippet:

```yaml
- name: web_fetch
  type: agent.tools.web_fetch.WebFetchTool
  args:
    base_url: http://webfetch:8081
    include_html: false
    summary_max_chars: 1200
```

The actual loader handles missing files gracefully and supports aliasing tool
names in configuration. See `agent.tools.loader` for implementation details.

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
from agent.tools.registry import ToolRegistry
from agent.tools.web_fetch import WebFetchTool

registry = ToolRegistry([WebFetchTool(base_url="http://webfetch:8081")])
result = await registry.get("web_fetch").run("https://example.com")
```
