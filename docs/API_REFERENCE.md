# API Reference

**Version:** 1.0.0
**Last Updated:** 2026-02-07

This document provides a comprehensive reference for all HTTP endpoints in the AI Agent Platform.

---

## Table of Contents

1. [Authentication](#authentication)
2. [Agent API](#agent-api)
3. [Chat Completions](#chat-completions)
4. [Model API](#model-api)
5. [OAuth API](#oauth-api)
6. [Admin Portal](#admin-portal)
7. [Diagnostic API](#diagnostic-api)
8. [Health Endpoints](#health-endpoints)

---

## Authentication

### Overview

The platform supports multiple authentication methods depending on the endpoint category:

| Endpoint Type | Authentication Method |
|---------------|----------------------|
| Agent API | API Key or Session |
| Admin Portal | Entra ID OAuth2 |
| Diagnostic API | X-API-Key header or Entra ID session |
| Health Endpoints | None (public) |

### API Key Authentication

For programmatic access to diagnostic endpoints:

```bash
curl -H "X-API-Key: your-api-key" https://your-domain/platformadmin/api/status
```

**Environment Variable:** `AGENT_DIAGNOSTIC_API_KEY`

### Entra ID Authentication

Admin portal uses Microsoft Entra ID (Azure AD) for authentication:

1. User visits `/platformadmin/`
2. Redirected to Entra ID for login
3. Callback to `/platformadmin/auth/callback`
4. Session cookie set for subsequent requests

---

## Agent API

### POST /v1/agent

Execute an agent task with conversation history support.

**Request Body:**

```json
{
  "prompt": "Your task or question",
  "conversation_id": "optional-conversation-uuid",
  "metadata": {
    "context_id": "optional-context-uuid"
  },
  "messages": [
    {
      "role": "user",
      "content": "Previous message"
    }
  ]
}
```

**Response:**

```json
{
  "response": "Agent's response",
  "conversation_id": "conversation-uuid",
  "created_at": "2026-02-07T12:00:00Z",
  "steps": [
    {
      "action": "skill",
      "tool": "researcher",
      "result": "step result"
    }
  ],
  "metadata": {
    "context_id": "context-uuid"
  }
}
```

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - Invalid request format
- `502 Bad Gateway` - Upstream service unavailable
- `500 Internal Server Error` - Server error

---

### GET /v1/agent/history/{conversation_id}

Retrieve conversation history.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `conversation_id` | string (UUID) | Conversation identifier |

**Response:**

```json
[
  {
    "role": "user",
    "content": "User message",
    "created_at": "2026-02-07T12:00:00Z"
  },
  {
    "role": "assistant",
    "content": "Agent response",
    "created_at": "2026-02-07T12:00:01Z"
  }
]
```

**Status Codes:**

- `200 OK` - Success
- `500 Internal Server Error` - Failed to retrieve history

---

## Chat Completions

### POST /v1/chat/completions

OpenAI-compatible chat completion endpoint with streaming support.

**Authentication:** X-OpenWebUI-User-Email, X-OpenWebUI-User-Name headers (optional)

**Request Body:**

```json
{
  "model": "gpt-3.5-turbo",
  "messages": [
    {
      "role": "user",
      "content": "Your message"
    }
  ],
  "stream": true,
  "chat_id": "optional-chat-uuid",
  "metadata": {
    "conversation_id": "optional-uuid"
  }
}
```

**Response (Non-streaming):**

```json
{
  "id": "chatcmpl-uuid",
  "object": "chat.completion",
  "created": 1707307200,
  "model": "gpt-3.5-turbo",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop",
      "message": {
        "role": "assistant",
        "content": "Response text",
        "metadata": {
          "steps": []
        }
      }
    }
  ],
  "steps": [],
  "metadata": {}
}
```

**Response (Streaming):**

Server-sent events (SSE) with format:

```
data: {"id":"chatcmpl-uuid","object":"chat.completion.chunk","created":1707307200,"model":"gpt-3.5-turbo","choices":[{"index":0,"delta":{"content":"chunk"},"finish_reason":null}]}

data: [DONE]
```

**Verbosity Modes:**

- **DEFAULT** - Minimal output (final answer only)
- **VERBOSE** - Detailed output (thinking, steps, tool calls) - Add `[VERBOSE]` to message
- **DEBUG** - Technical output (raw JSON chunks) - Add `[DEBUG]` to message

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - No user message found
- `502 Bad Gateway` - Upstream service unavailable

---

### POST /v1/agent/chat/completions

Legacy endpoint, same as `/v1/chat/completions`.

### POST /chat/completions

Legacy endpoint, same as `/v1/chat/completions`.

---

## Model API

### GET /v1/models

List available models.

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "openai/gpt-oss-120b:exacto",
      "object": "model",
      "created": 1707307200,
      "owned_by": "openrouter"
    }
  ]
}
```

**Status Codes:**

- `200 OK` - Success
- `502 Bad Gateway` - LiteLLM gateway error

### GET /models

Legacy endpoint, same as `/v1/models`.

---

## OAuth API

User-facing OAuth endpoints for tool authorization.

### POST /auth/oauth/authorize

Start OAuth authorization flow.

**Authentication:** User session required (via `verify_user`)

**Request Body:**

```json
{
  "provider": "homey",
  "context_id": "context-uuid"
}
```

**Response:**

```json
{
  "authorization_url": "https://provider.com/oauth/authorize?...",
  "state": "csrf-state-token",
  "message": "To authorize Homey, please click this link:\n\n[Authorize Homey](https://...)"
}
```

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - Provider not configured
- `403 Forbidden` - No access to context
- `500 Internal Server Error` - Authorization failed

---

### GET /auth/oauth/callback

OAuth provider callback handler (NO AUTH - external redirect).

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | string | Authorization code from provider |
| `state` | string | CSRF protection state |
| `error` | string | Error if user denied |

**Response:** HTML page showing success or error

**Status Codes:**

- `200 OK` - Authorization successful
- `400 Bad Request` - User cancelled or error
- `500 Internal Server Error` - Token exchange failed

---

### POST /auth/oauth/revoke

Revoke and delete OAuth token.

**Authentication:** User session required

**Request Body:**

```json
{
  "provider": "homey",
  "context_id": "context-uuid"
}
```

**Response:**

```json
{
  "status": "revoked",
  "provider": "homey"
}
```

**Status Codes:**

- `200 OK` - Success
- `403 Forbidden` - No access to context
- `500 Internal Server Error` - Revocation failed

---

### POST /auth/oauth/status

Check OAuth token status.

**Authentication:** User session required

**Request Body:**

```json
{
  "provider": "homey",
  "context_id": "context-uuid"
}
```

**Response:**

```json
{
  "provider": "homey",
  "context_id": "context-uuid",
  "has_token": true,
  "is_valid": true
}
```

**Status Codes:**

- `200 OK` - Success
- `403 Forbidden` - No access to context
- `500 Internal Server Error` - Status check failed

---

## Admin Portal

All admin endpoints require Entra ID authentication and CSRF protection (for POST/DELETE).

### GET /platformadmin/

Admin portal dashboard.

**Authentication:** Entra ID session

**Response:** HTML dashboard with navigation to all admin sections

**Status Codes:**

- `200 OK` - Success
- `302 Found` - Redirect to Entra ID login

---

### Contexts

#### GET /platformadmin/contexts/

Context management dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/contexts

List all contexts (JSON API).

**Authentication:** Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `type_filter` | string | Optional context type filter |

**Response:**

```json
{
  "contexts": [
    {
      "id": "uuid",
      "name": "context-name",
      "type": "personal",
      "config": {},
      "pinned_files": [],
      "default_cwd": "/tmp",
      "conversation_count": 5,
      "oauth_token_count": 2,
      "tool_permission_count": 3
    }
  ],
  "total": 1
}
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/contexts/{context_id}

Get detailed context information.

**Authentication:** Entra ID session

**Response:**

```json
{
  "id": "uuid",
  "name": "context-name",
  "type": "personal",
  "config": {},
  "pinned_files": [],
  "default_cwd": "/tmp",
  "conversations": [],
  "oauth_tokens": [],
  "tool_permissions": []
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Context not found

---

#### POST /platformadmin/contexts

Create a new context.

**Authentication:** Entra ID session + CSRF token

**Request Body:**

```json
{
  "name": "my-context",
  "type": "virtual",
  "config": {},
  "pinned_files": [],
  "default_cwd": "/tmp"
}
```

**Response:**

```json
{
  "success": true,
  "message": "Created context 'my-context'",
  "context_id": "uuid"
}
```

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - Context name already exists

---

#### DELETE /platformadmin/contexts/{context_id}

Delete a context and all related data.

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
{
  "success": true,
  "message": "Deleted context 'my-context' and all related data",
  "deleted_context_id": "uuid"
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Context not found

---

### Credentials

#### GET /platformadmin/credentials/

Credential management dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/credentials/list

List all user credentials.

**Authentication:** Entra ID session

**Response:**

```json
{
  "credentials": [
    {
      "id": "uuid",
      "user_id": "uuid",
      "user_email": "user@example.com",
      "credential_type": "azure_devops_pat",
      "credential_type_name": "Azure DevOps PAT",
      "metadata": {
        "organization_url": "https://dev.azure.com/org"
      },
      "created_at": "2026-02-07T12:00:00Z",
      "updated_at": "2026-02-07T12:00:00Z"
    }
  ],
  "total_credentials": 1,
  "users_with_credentials": 1
}
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/credentials/user/{user_id}

Get credentials for a specific user.

**Authentication:** Entra ID session

**Response:**

```json
{
  "user_id": "uuid",
  "user_email": "user@example.com",
  "credentials": [...]
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - User not found

---

#### GET /platformadmin/credentials/types

List available credential types.

**Authentication:** Entra ID session

**Response:**

```json
{
  "types": {
    "azure_devops_pat": {
      "name": "Azure DevOps PAT",
      "description": "Personal Access Token for Azure DevOps",
      "placeholder": "Enter your Azure DevOps PAT",
      "metadata_fields": [...]
    }
  }
}
```

**Status Codes:**

- `200 OK` - Success

---

#### POST /platformadmin/credentials/create

Create or update a user credential.

**Authentication:** Entra ID session + CSRF token

**Request Body:**

```json
{
  "user_id": "uuid",
  "credential_type": "azure_devops_pat",
  "value": "encrypted-value",
  "metadata": {
    "organization_url": "https://dev.azure.com/org"
  }
}
```

**Response:**

```json
{
  "success": true,
  "message": "Credential azure_devops_pat saved for user@example.com",
  "credential_id": "uuid"
}
```

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - Invalid credential type
- `404 Not Found` - User not found

---

#### DELETE /platformadmin/credentials/{credential_id}

Delete a credential.

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
{
  "success": true,
  "message": "Deleted azure_devops_pat credential for user@example.com"
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Credential not found

---

### OAuth Tokens

#### GET /platformadmin/oauth/

OAuth token management dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/oauth/tokens

List all OAuth tokens.

**Authentication:** Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `context_id` | UUID | Optional context filter |
| `provider` | string | Optional provider filter |

**Response:**

```json
{
  "tokens": [
    {
      "id": "uuid",
      "context_id": "uuid",
      "provider": "homey",
      "token_type": "Bearer",
      "expires_at": "2026-03-07T12:00:00Z",
      "scope": "flow",
      "is_expired": false,
      "has_refresh_token": true,
      "created_at": "2026-02-07T12:00:00Z",
      "updated_at": "2026-02-07T12:00:00Z"
    }
  ],
  "total": 1
}
```

**Status Codes:**

- `200 OK` - Success

---

#### DELETE /platformadmin/oauth/tokens/{token_id}

Revoke an OAuth token.

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
{
  "success": true,
  "message": "Revoked homey OAuth token for context uuid",
  "revoked_token_id": "uuid"
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Token not found

---

#### GET /platformadmin/oauth/status/{context_id}

Get OAuth authorization status for a context.

**Authentication:** Entra ID session

**Response:**

```json
{
  "context_id": "uuid",
  "providers": [
    {
      "provider": "homey",
      "authorized": true,
      "expires_at": "2026-03-07T12:00:00Z",
      "is_expired": false,
      "has_refresh_token": true,
      "scope": "flow"
    }
  ],
  "total_providers": 1
}
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/oauth/callback

OAuth provider callback (NO AUTH - external redirect).

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | string | Authorization code |
| `state` | string | CSRF state |
| `error` | string | Error if denied |

**Response:** HTML success/error page

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - Error or user denial
- `500 Internal Server Error` - Token exchange failed

---

#### GET /platformadmin/oauth/initiate/{provider}

Initiate OAuth flow for admin user's personal context.

**Authentication:** Entra ID session

**Response:** Redirect to OAuth provider

**Status Codes:**

- `302 Found` - Redirect to provider
- `400 Bad Request` - Provider not configured
- `500 Internal Server Error` - Initiation failed

---

### Workspaces

#### GET /platformadmin/workspaces/

Workspace management dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/workspaces/list

List all workspaces.

**Authentication:** Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `context_id` | UUID | Optional context filter |

**Response:**

```json
{
  "workspaces": [
    {
      "id": "uuid",
      "context_id": "uuid",
      "context_name": "my-context",
      "name": "repo-name",
      "repo_url": "https://github.com/org/repo.git",
      "branch": "main",
      "local_path": "/tmp/agent-workspaces/context-uuid/repo-name",
      "status": "cloned",
      "last_synced_at": "2026-02-07T12:00:00Z",
      "sync_error": null,
      "created_at": "2026-02-07T11:00:00Z"
    }
  ],
  "total": 1
}
```

**Status Codes:**

- `200 OK` - Success

---

#### POST /platformadmin/workspaces

Create a new workspace by cloning a repository.

**Authentication:** Entra ID session + CSRF token

**Request Body:**

```json
{
  "context_id": "uuid",
  "repo_url": "https://github.com/org/repo.git",
  "name": "optional-name",
  "branch": "main"
}
```

**Response:**

```json
{
  "success": true,
  "message": "Cloned repository to /tmp/agent-workspaces/...",
  "workspace_id": "uuid"
}
```

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - Workspace already exists
- `404 Not Found` - Context not found
- `500 Internal Server Error` - Clone failed
- `504 Gateway Timeout` - Clone timed out

---

#### POST /platformadmin/workspaces/{workspace_id}/sync

Sync a workspace (pull latest changes).

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
{
  "success": true,
  "message": "Workspace synced successfully"
}
```

**Status Codes:**

- `200 OK` - Success
- `400 Bad Request` - Workspace directory not found
- `404 Not Found` - Workspace not found
- `500 Internal Server Error` - Sync failed
- `504 Gateway Timeout` - Sync timed out

---

#### DELETE /platformadmin/workspaces/{workspace_id}

Delete a workspace and its local files.

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
{
  "success": true,
  "message": "Deleted workspace 'repo-name'"
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Workspace not found

---

### MCP Integrations

#### GET /platformadmin/mcp/

MCP integrations dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/mcp/integrations

Get configured MCP integrations per context.

**Authentication:** Entra ID session

**Response:**

```json
{
  "integrations": [
    {
      "context_id": "uuid",
      "context_name": "my-context",
      "provider": "Homey",
      "type": "oauth",
      "status": "active",
      "updated_at": "2026-02-07T12:00:00Z"
    }
  ]
}
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/mcp/activity

Get recent MCP connection events.

**Authentication:** Entra ID session

**Response:**

```json
{
  "events": [
    {
      "timestamp": "2026-02-07T12:00:00Z",
      "context_id": "uuid",
      "provider": "homey",
      "result": "connected",
      "tools_count": 5,
      "error": null,
      "transport": "sse"
    }
  ]
}
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/mcp/health

Get MCP client pool health status.

**Authentication:** Entra ID session

**Response:**

```json
{
  "health": {
    "total_contexts": 2,
    "total_clients": 3,
    "status": "healthy"
  }
}
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/mcp/stats

Get MCP client pool statistics.

**Authentication:** Entra ID session

**Response:**

```json
{
  "stats": {
    "total_clients": 3,
    "contexts": 2,
    "uptime_seconds": 3600
  }
}
```

**Status Codes:**

- `200 OK` - Success

---

#### POST /platformadmin/mcp/disconnect/{context_id}

Force disconnect all MCP clients for a context.

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
{
  "success": true,
  "message": "Disconnected all MCP clients for context uuid",
  "context_id": "uuid"
}
```

**Status Codes:**

- `200 OK` - Success
- `503 Service Unavailable` - MCP pool not initialized

---

### Users

#### GET /platformadmin/users/

User management dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/users/list

List all users.

**Authentication:** Entra ID session or API key

**Response:**

```json
[
  {
    "id": "uuid",
    "email": "user@example.com",
    "display_name": "User Name",
    "role": "user",
    "is_active": true,
    "created_at": "2026-01-01T00:00:00Z",
    "last_login_at": "2026-02-07T12:00:00Z",
    "context_count": 2
  }
]
```

**Status Codes:**

- `200 OK` - Success

---

### Permissions

#### GET /platformadmin/permissions/

Tool permissions dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/permissions/contexts

List contexts with permission summary.

**Authentication:** Entra ID session

**Response:**

```json
{
  "contexts": [
    {
      "context_id": "uuid",
      "context_name": "my-context",
      "context_type": "personal",
      "users": [
        {
          "display_name": "User",
          "email": "user@example.com",
          "role": "owner"
        }
      ],
      "permission_count": 5,
      "allowed_count": 3,
      "denied_count": 2,
      "state": "customized"
    }
  ]
}
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/permissions/contexts/{context_id}

Get detailed permissions for a context.

**Authentication:** Entra ID session

**Response:**

```json
{
  "context_id": "uuid",
  "context_name": "my-context",
  "state": "customized",
  "tools": [
    {
      "tool_name": "web_search",
      "tool_description": "Search the web",
      "allowed": true,
      "has_explicit_permission": true
    }
  ]
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Context not found

---

#### POST /platformadmin/permissions/contexts/{context_id}/tools/{tool_name}

Set tool permission for a context.

**Authentication:** Entra ID session + CSRF token

**Request Body:**

```json
{
  "allowed": true
}
```

**Response:**

```json
{
  "success": true,
  "message": "Permission set: web_search = allowed"
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Context not found

---

#### DELETE /platformadmin/permissions/contexts/{context_id}/tools/{tool_name}

Remove explicit tool permission (revert to default).

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
{
  "success": true,
  "message": "Permission removed: web_search (reverted to default)"
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Context not found

---

### Price Tracker

#### GET /platformadmin/price-tracker/

Price tracker dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/price-tracker/stores

List all configured stores.

**Authentication:** Entra ID session

**Response:**

```json
[
  {
    "id": "uuid",
    "name": "Prisjakt",
    "slug": "prisjakt",
    "type": "prisjakt",
    "is_active": true
  }
]
```

**Status Codes:**

- `200 OK` - Success

---

### Diagnostics

#### GET /platformadmin/diagnostics/

Diagnostics dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

#### GET /platformadmin/diagnostics/traces

Get recent traces.

**Authentication:** Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Max traces (default: 1000) |
| `show_all` | bool | Include health traces (default: false) |

**Response:**

```json
[
  {
    "trace_id": "trace-uuid",
    "start_time": "2026-02-07T12:00:00Z",
    "total_duration_ms": 150.5,
    "status": "OK",
    "root": {
      "name": "Agent Task",
      "span_id": "span-uuid"
    },
    "spans": [...]
  }
]
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/diagnostics/metrics

Get system health metrics.

**Authentication:** Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `window` | int | Traces to analyze (default: 60) |

**Response:**

```json
{
  "total_requests": 100,
  "error_rate": 0.05,
  "avg_duration_ms": 150.0,
  "insights": ["5% error rate detected"]
}
```

**Status Codes:**

- `200 OK` - Success

---

#### POST /platformadmin/diagnostics/run

Run integration tests on all components.

**Authentication:** Entra ID session + CSRF token

**Response:**

```json
[
  {
    "component": "PostgreSQL",
    "status": "healthy",
    "latency_ms": 5.0,
    "details": null
  }
]
```

**Status Codes:**

- `200 OK` - Success

---

#### GET /platformadmin/diagnostics/summary

Get diagnostics summary.

**Authentication:** Entra ID session

**Response:**

```json
{
  "overall_status": "HEALTHY",
  "healthy_components": ["PostgreSQL", "Qdrant"],
  "failed_components": [],
  "metrics": {
    "total_requests": 100
  },
  "recommended_actions": []
}
```

**Status Codes:**

- `200 OK` - Success

---

### Debug Logs

#### GET /platformadmin/debug/

Debug log dashboard (HTML).

**Authentication:** Entra ID session

**Response:** HTML page

---

## Diagnostic API

Programmatic API for AI agents and scripts. Supports X-API-Key authentication.

### GET /platformadmin/api/status

Get aggregated system status.

**Authentication:** X-API-Key header or Entra ID session

**Response:**

```json
{
  "status": "HEALTHY",
  "timestamp": "2026-02-07T12:00:00Z",
  "healthy_components": ["PostgreSQL", "Qdrant", "LiteLLM"],
  "failed_components": [],
  "recent_errors": [],
  "metrics": {
    "total_requests": 150,
    "error_rate": 0.02
  },
  "recommended_actions": []
}
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/conversations

List conversations with message counts.

**Authentication:** X-API-Key header or Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Max results (default: 50, max: 200) |
| `offset` | int | Skip count (default: 0) |
| `context_id` | UUID | Optional context filter |

**Response:**

```json
[
  {
    "id": "uuid",
    "context_id": "uuid",
    "created_at": "2026-02-07T12:00:00Z",
    "updated_at": "2026-02-07T13:00:00Z",
    "message_count": 10,
    "metadata": {}
  }
]
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/conversations/{conversation_id}/messages

Get messages for a conversation.

**Authentication:** X-API-Key header or Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Max messages (default: 100, max: 500) |
| `offset` | int | Skip count (default: 0) |
| `role` | string | Filter by role (user, assistant, system) |

**Response:**

```json
{
  "conversation_id": "uuid",
  "messages": [
    {
      "id": "uuid",
      "role": "user",
      "content": "Message text",
      "created_at": "2026-02-07T12:00:00Z",
      "trace_id": "trace-uuid"
    }
  ],
  "total_count": 10
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Conversation not found
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/debug/stats

Get debug log statistics.

**Authentication:** X-API-Key header or Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `hours` | int | Hours of data (default: 24, max: 168) |

**Response:**

```json
{
  "total_logs": 500,
  "by_event_type": {
    "supervisor": 100,
    "tool_call": 200
  },
  "by_hour": [
    {
      "hour": "2026-02-07T12:00:00",
      "count": 50
    }
  ],
  "recent_errors": [
    {
      "trace_id": "uuid",
      "outcome": "ABORT",
      "reason": "Tool failed",
      "step": "step-1",
      "created_at": "2026-02-07T12:00:00Z"
    }
  ]
}
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/traces/search

Search OpenTelemetry traces.

**Authentication:** X-API-Key header or Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `trace_id` | string | Partial trace ID match |
| `min_duration_ms` | float | Minimum duration filter |
| `status` | string | Filter by status (OK, ERR) |
| `limit` | int | Max results (default: 50, max: 200) |

**Response:**

```json
[
  {
    "trace_id": "trace-uuid",
    "start_time": "2026-02-07T12:00:00Z",
    "duration_ms": 150.5,
    "status": "OK",
    "root_name": "Agent Task",
    "span_count": 5
  }
]
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/traces/{trace_id}

Get full trace detail with all spans.

**Authentication:** X-API-Key header or Entra ID session

**Response:**

```json
{
  "trace_id": "trace-uuid",
  "start_time": "2026-02-07T12:00:00Z",
  "duration_ms": 150.5,
  "status": "OK",
  "root_name": "Agent Task",
  "span_count": 5,
  "spans": [
    {
      "span_id": "span-uuid",
      "parent_id": null,
      "name": "Agent Task",
      "start_time": "2026-02-07T12:00:00Z",
      "duration_ms": 150.5,
      "status": "OK",
      "attributes": {}
    }
  ]
}
```

**Status Codes:**

- `200 OK` - Success
- `404 Not Found` - Trace not found
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/config

Get system configuration entries.

**Authentication:** X-API-Key header or Entra ID session

**Response:**

```json
[
  {
    "key": "config_key",
    "value": "value",
    "description": "Configuration description",
    "updated_at": "2026-02-07T12:00:00Z"
  }
]
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/tools/stats

Get tool execution statistics.

**Authentication:** X-API-Key header or Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `hours` | int | Hours to analyze (default: 24, range: 1-168) |

**Response:**

```json
{
  "period_hours": 24,
  "tools": {
    "web_search": {
      "count": 50,
      "total_duration_ms": 5000.0,
      "avg_duration_ms": 100.0,
      "max_duration_ms": 500.0,
      "min_duration_ms": 50.0,
      "timed_count": 50
    }
  },
  "total_tool_calls": 50
}
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/skills/stats

Get skill step execution statistics.

**Authentication:** X-API-Key header or Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `hours` | int | Hours to analyze (default: 24, range: 1-168) |

**Response:**

```json
{
  "period_hours": 24,
  "skills": {
    "researcher": {
      "count": 20,
      "total_duration_ms": 3000.0,
      "avg_duration_ms": 150.0,
      "max_duration_ms": 500.0,
      "min_duration_ms": 100.0,
      "timed_count": 20,
      "outcomes": {
        "SUCCESS": 18,
        "RETRY": 1,
        "REPLAN": 1,
        "ABORT": 0,
        "unknown": 0
      }
    }
  },
  "total_skill_steps": 20
}
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/requests/stats

Get HTTP request timing statistics.

**Authentication:** X-API-Key header or Entra ID session

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `hours` | int | Hours to analyze (default: 24, range: 1-168) |

**Response:**

```json
{
  "period_hours": 24,
  "endpoints": {
    "/v1/agent": {
      "count": 100,
      "avg_duration_ms": 150.0,
      "max_duration_ms": 500.0,
      "total_duration_ms": 15000.0
    }
  },
  "total_requests": 100
}
```

**Status Codes:**

- `200 OK` - Success
- `401 Unauthorized` - Missing or invalid API key

---

### GET /platformadmin/api/health

Simple health check (no auth required).

**Response:**

```json
{
  "status": "ok",
  "service": "diagnostic-api"
}
```

**Status Codes:**

- `200 OK` - Service is healthy

---

## Health Endpoints

### GET /healthz

Basic health check.

**Authentication:** None (public)

**Response:**

```json
{
  "status": "ok"
}
```

**Status Codes:**

- `200 OK` - Service is healthy

---

## Error Responses

All endpoints follow consistent error response format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common Status Codes

| Code | Meaning | Description |
|------|---------|-------------|
| `200 OK` | Success | Request completed successfully |
| `201 Created` | Created | Resource created successfully |
| `302 Found` | Redirect | Temporary redirect to another location |
| `400 Bad Request` | Client Error | Invalid request parameters or body |
| `401 Unauthorized` | Authentication Required | Missing or invalid authentication |
| `403 Forbidden` | Authorization Failed | Authenticated but not authorized |
| `404 Not Found` | Resource Missing | Requested resource does not exist |
| `422 Unprocessable Entity` | Validation Error | Request validation failed |
| `500 Internal Server Error` | Server Error | Unexpected server error |
| `502 Bad Gateway` | Upstream Error | Upstream service (LiteLLM) unavailable |
| `503 Service Unavailable` | Service Down | Service temporarily unavailable |
| `504 Gateway Timeout` | Timeout | Operation timed out |

---

## Rate Limiting

Rate limiting is disabled in test environment but active in production.

**Configuration:**
- Implementation: SlowAPI
- Default limits defined per endpoint
- Response headers include rate limit information

**Response Headers:**
- `X-RateLimit-Limit` - Maximum requests allowed
- `X-RateLimit-Remaining` - Requests remaining in window
- `X-RateLimit-Reset` - Time when limit resets

**Rate Limit Exceeded Response:**

```json
{
  "detail": "Rate limit exceeded"
}
```

**Status Code:** `429 Too Many Requests`

---

## Security Headers

All responses include security headers:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevent MIME sniffing |
| `X-XSS-Protection` | `0` | Disable legacy XSS filter |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Control referrer info |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Disable features |
| `X-Frame-Options` | `DENY` or `SAMEORIGIN` | Prevent clickjacking |
| `Strict-Transport-Security` | Production only | Force HTTPS |

**X-Frame-Options:**
- `SAMEORIGIN` for `/platformadmin/*` (admin portal)
- `DENY` for all other endpoints

---

## CORS Configuration

Configured via `AGENT_CORS_ALLOWED_ORIGINS` environment variable.

**Settings:**
- `allow_credentials: true`
- `allow_methods: *`
- `allow_headers: *`

---

## Versioning

**Current Version:** 1.0.0

API versioning follows these patterns:

- Core agent API: `/v1/agent`
- Chat completions: `/v1/chat/completions`
- Models: `/v1/models`
- Legacy endpoints: No version prefix

**Note:** Admin portal and diagnostic API endpoints are not versioned.

---

## Changelog

### 2026-02-07 - v1.0.0

- Initial comprehensive API documentation
- Documented all agent, admin, and diagnostic endpoints
- Added authentication and security details
- Included request/response examples
