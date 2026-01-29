# Open WebUI — Agent Integration

Open WebUI is pre-configured (via `docker-compose.yml`) to send all chat
traffic to the FastAPI agent using the OpenAI-compatible
`/v1/chat/completions` endpoint. The agent responds with structured payloads
(`steps`, `response`, `metadata`), enabling the UI to visualise reasoning
chains. The steps below remain useful when you want to publish additional
presets or expose explicit tool triggers from the UI.

## Prerequisites
- Stack running via `python -m stack up` with services healthy.
- Open WebUI user account with permission to create tools and presets.
- Agent API reachable at `http://agent:8000/v1/agent` inside the Docker network (or `http://localhost:8000/v1/agent` from the host).

## Optional: Explicit Tool Presets
The agent automatically determines tool usage based on metadata embedded in
Open WebUI prompts. When you need deterministic behaviour (for example, a
research preset that always requests `web_fetch`), create a REST tool and map
it to a preset:

1. Log in to Open WebUI and open **Tools → Create Tool**.
2. Choose type **REST** and set the name to `agent_research`.
3. Populate the form:

| Field | Value |
| --- | --- |
| Description | `Force tool metadata for the agent` |
| Method | `POST` |
| URL | `http://agent:8000/v1/agent` |
| Headers | `Content-Type: application/json` |
| Body Template | see below |
| Timeout | `30` seconds |
| Authentication | `None` |

## Research Preset with Tools
To force tool usage, duplicate the steps above with a new tool named `agent_research` and use the body template:
```json
{
  "prompt": "<research question>",
  "metadata": {
    "tools": ["web_fetch"],
    "tool_calls": [
      {
        "name": "web_fetch",
        "args": {"url": "https://example.com"}
      }
    ]
  }
}
```
Attach this tool to the **Research** preset so that requests explicitly enable the `web_fetch` tool registered in `config/tools.yaml`.

> **Tip:** For general conversations you do not need a custom tool—Open WebUI
> calls `/v1/chat/completions` directly and the agent manages tool execution
> automatically.

## Exporting Configuration
After updating tools or presets, export the Open WebUI configuration from **Admin → Settings → Export** and commit the resulting SQLite dump (`openwebui/export/app.db.sql`) so changes are reproducible.

## Verification Checklist
- Tool calls return HTTP 200 with a JSON payload containing `conversation_id` and `response`.
- `python -m stack logs agent` shows tool metadata when `web_fetch` is requested.
- Capability catalog (`capabilities/catalog.yaml`) reflects newly exposed tools or presets.
