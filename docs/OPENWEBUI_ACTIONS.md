# Open WebUI — Actions tool to n8n

This document explains how to configure Open WebUI to call the platform's n8n webhook. The goal is a reusable tool (`n8n_action`) that sends a JSON payload to `/webhook/agent` and shows the reply in chat.

## Prerequisites
- The stack runs per `compose/docker-compose.yml`.
- Your Open WebUI account can create tools.
- The n8n workflow "Agent Echo" is active (see `flows/workflows/`).

## UI configuration
1. Log in to Open WebUI, open Tools -> Create Tool.
2. Choose type "REST" and name it `n8n_action`.
3. Fill in settings:

| Field | Value |
| --- | --- |
| Description | `POST JSON to n8n agent webhook` |
| Method | `POST` |
| URL | `http://n8n:5678/webhook/agent` |
| Headers | `Content-Type: application/json` |
| Body Template | `{ "action": "agent.echo", "args": { "message": "<your message>" } }` |
| Timeout | `15` seconds |
| Authentication | `None` |

4. Save the tool and enable it for the Actions preset if requested.
5. Open a new chat, choose the Actions preset, and run `n8n_action`. Change `message` in the body template to verify the reply is echoed.

Tip: The `agent.echo` contract is documented in `capabilities/catalog.yaml`.

## Presets for Qwen profiles

Swedish profile via LiteLLM (`local/qwen2.5-sv`):
1. Open Presets -> Create Preset.
2. Name: `Swedish - Qwen 2.5`
3. Provider: `OpenAI Compatible` (pointed at LiteLLM)
4. Model: `local/qwen2.5-sv`
5. Temperature: `0.4` (optional)
6. Save and make visible.

English profile via LiteLLM (`local/qwen2.5-en`):
1. Open Presets -> Create Preset.
2. Name: `English - Qwen 2.5`
3. Provider: `OpenAI Compatible` (via LiteLLM)
4. Model: `local/qwen2.5-en`
5. Temperature: `0.35` (optional)
6. Save and make visible.

After adding tools/presets, run `./scripts/OpenWebUI-Config.ps1 export` and commit `openwebui/export/app.db.sql` to keep configuration reproducible.

## Verification
1. Run the n8n smoketest in `docs/OPERATIONS.md`.
2. Start Open WebUI, choose the Actions preset, and call `n8n_action`.
3. Verify the reply contains `"ok": true`, `"action": "agent.echo"`, and that your request arguments are returned in `received.args`.

