# Admin API Reference

The Admin API provides management endpoints for multi-tenant operations, secured with API key authentication.

## Table of Contents

- [Authentication](#authentication)
- [Context Management](#context-management)
- [OAuth Token Management](#oauth-token-management)
- [MCP Client Management](#mcp-client-management)
- [Diagnostics](#diagnostics)
- [Error Handling](#error-handling)

---

## Authentication

All admin endpoints require an API key passed via the `X-API-Key` header.

### Setup

```bash
# Generate a secure API key
openssl rand -hex 32

# Add to .env
AGENT_ADMIN_API_KEY=your_generated_key_here

# Restart the agent service
```

### Usage

```bash
# All admin requests require this header
curl -H "X-API-Key: your_generated_key_here" \
  http://localhost:8000/admin/contexts
```

### Error Responses

**401 Unauthorized** - Missing or invalid API key:
```json
{
  "detail": "Missing X-API-Key header"
}
```

**503 Service Unavailable** - Admin API key not configured:
```json
{
  "detail": "Admin API key not configured. Set AGENT_ADMIN_API_KEY environment variable."
}
```

---

## Context Management

Manage isolated tenant contexts (workspaces/projects).

### List Contexts

Get all contexts with summary statistics.

```http
GET /admin/contexts
```

**Query Parameters:**
- `type_filter` (optional): Filter by context type (e.g., "virtual", "git_repo")

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  "http://localhost:8000/admin/contexts?type_filter=virtual"
```

**Response:**
```json
{
  "contexts": [
    {
      "id": "abc-123-def-456",
      "name": "production",
      "type": "devops",
      "config": {},
      "pinned_files": [],
      "default_cwd": "/app",
      "conversation_count": 15,
      "oauth_token_count": 2,
      "tool_permission_count": 3
    }
  ],
  "total": 1
}
```

### Get Context Details

Get detailed information about a specific context.

```http
GET /admin/contexts/{context_id}
```

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/contexts/abc-123-def-456
```

**Response:**
```json
{
  "id": "abc-123-def-456",
  "name": "production",
  "type": "devops",
  "config": {"env": "prod"},
  "pinned_files": ["/app/config.yaml"],
  "default_cwd": "/app",
  "conversations": [
    {
      "id": "conv-789",
      "platform": "openwebui",
      "platform_id": "user_123",
      "current_cwd": "/app",
      "created_at": "2025-01-06T10:00:00Z"
    }
  ],
  "oauth_tokens": [
    {
      "id": "token-456",
      "provider": "homey",
      "token_type": "Bearer",
      "expires_at": "2025-01-07T10:00:00Z",
      "is_expired": false,
      "has_refresh_token": true,
      "scope": "read write",
      "created_at": "2025-01-06T09:00:00Z"
    }
  ],
  "tool_permissions": [
    {
      "id": "perm-321",
      "tool_name": "bash",
      "allowed": false,
      "created_at": "2025-01-06T08:00:00Z"
    }
  ]
}
```

### Create Context

Create a new isolated context.

```http
POST /admin/contexts
```

**Request Body:**
```json
{
  "name": "staging",
  "type": "devops",
  "config": {"env": "staging"},
  "pinned_files": [],
  "default_cwd": "/app"
}
```

**Example:**
```bash
curl -X POST \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "staging",
    "type": "devops",
    "default_cwd": "/app"
  }' \
  http://localhost:8000/admin/contexts
```

**Response:**
```json
{
  "success": true,
  "message": "Created context 'staging'",
  "context_id": "new-context-uuid"
}
```

**Error - Duplicate Name:**
```json
{
  "detail": "Context with name 'staging' already exists"
}
```

### Delete Context

Delete a context and all related data (conversations, OAuth tokens, permissions).

```http
DELETE /admin/contexts/{context_id}
```

**Example:**
```bash
curl -X DELETE \
  -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/contexts/abc-123-def-456
```

**Response:**
```json
{
  "success": true,
  "message": "Deleted context 'production' and all related data",
  "deleted_context_id": "abc-123-def-456"
}
```

**⚠️ Warning:** This is a destructive operation. All conversations, messages, OAuth tokens, and tool permissions for the context will be permanently deleted via CASCADE.

---

## OAuth Token Management

Manage OAuth tokens used for MCP integrations.

### List OAuth Tokens

Get all OAuth tokens (with sensitive data masked).

```http
GET /admin/oauth/tokens
```

**Query Parameters:**
- `context_id` (optional): Filter by context UUID
- `provider` (optional): Filter by provider name (e.g., "homey")

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  "http://localhost:8000/admin/oauth/tokens?provider=homey"
```

**Response:**
```json
{
  "tokens": [
    {
      "id": "token-123",
      "context_id": "abc-456",
      "provider": "homey",
      "token_type": "Bearer",
      "expires_at": "2025-01-07T10:00:00Z",
      "scope": "read write",
      "is_expired": false,
      "has_refresh_token": true,
      "created_at": "2025-01-06T09:00:00Z",
      "updated_at": "2025-01-06T09:00:00Z"
    }
  ],
  "total": 1
}
```

**Note:** `access_token` and `refresh_token` are **never** exposed in admin responses for security.

### Revoke OAuth Token

Revoke (delete) an OAuth token and invalidate MCP client cache.

```http
DELETE /admin/oauth/tokens/{token_id}
```

**Example:**
```bash
curl -X DELETE \
  -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/oauth/tokens/token-123
```

**Response:**
```json
{
  "success": true,
  "message": "Revoked homey OAuth token for context abc-456",
  "revoked_token_id": "token-123"
}
```

**Side Effects:**
- Token deleted from database
- MCP clients for the context disconnected (forces re-auth on next request)

### Get OAuth Status

Get OAuth authorization status for a context.

```http
GET /admin/oauth/status/{context_id}
```

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/oauth/status/abc-456
```

**Response:**
```json
{
  "context_id": "abc-456",
  "providers": [
    {
      "provider": "homey",
      "authorized": true,
      "expires_at": "2025-01-07T10:00:00Z",
      "is_expired": false,
      "has_refresh_token": true,
      "scope": "read write"
    }
  ],
  "total_providers": 1
}
```

---

## MCP Client Management

Manage Model Context Protocol (MCP) client connections.

### Get MCP Health

Get health status of all MCP clients across all contexts.

```http
GET /admin/mcp/health
```

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/mcp/health
```

**Response:**
```json
{
  "health": {
    "abc-456": {
      "clients": [
        {
          "name": "Homey",
          "connected": true,
          "state": "CONNECTED",
          "tools_count": 15,
          "resources_count": 0,
          "prompts_count": 0,
          "cache_stale": false
        }
      ],
      "total_clients": 1
    }
  }
}
```

### Get MCP Statistics

Get overall MCP client pool statistics.

```http
GET /admin/mcp/stats
```

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/mcp/stats
```

**Response:**
```json
{
  "stats": {
    "total_contexts": 3,
    "total_clients": 5,
    "connected_clients": 4,
    "disconnected_clients": 1
  }
}
```

### Disconnect MCP Clients

Force disconnect all MCP clients for a context.

```http
POST /admin/mcp/disconnect/{context_id}
```

**Use Cases:**
- Force re-authentication after OAuth token changes
- Recover from stuck connections
- Reset MCP client state

**Example:**
```bash
curl -X POST \
  -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/mcp/disconnect/abc-456
```

**Response:**
```json
{
  "success": true,
  "message": "Disconnected all MCP clients for context abc-456",
  "context_id": "abc-456"
}
```

**Side Effects:**
- All MCP clients disconnected
- Removed from client pool cache
- Next request will create fresh connections

---

## Diagnostics

System diagnostics and monitoring endpoints.

### Get Traces

Get recent request traces (for debugging and monitoring).

```http
GET /admin/diagnostics/traces
```

**Query Parameters:**
- `limit` (default: 1000): Maximum traces to return
- `show_all` (default: false): Include diagnostic/health traces

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  "http://localhost:8000/admin/diagnostics/traces?limit=10&show_all=true"
```

### Get Metrics

Get system health metrics.

```http
GET /admin/diagnostics/metrics
```

**Query Parameters:**
- `window` (default: 60): Number of traces to analyze

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/diagnostics/metrics
```

### Run Integration Tests

Run integration tests on all system components.

```http
POST /admin/diagnostics/run
```

**Example:**
```bash
curl -X POST \
  -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/diagnostics/run
```

**Response:**
```json
[
  {
    "component": "LiteLLM",
    "status": "ok",
    "latency_ms": 45.2,
    "message": null
  },
  {
    "component": "Qdrant",
    "status": "ok",
    "latency_ms": 12.5,
    "message": null
  }
]
```

### Get Diagnostics Summary

Get machine-readable diagnostics summary.

```http
GET /admin/diagnostics/summary
```

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/diagnostics/summary
```

### Get Crash Log

Get the last crash log (for debugging unhandled exceptions).

```http
GET /admin/diagnostics/crash-log
```

**Example:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/diagnostics/crash-log
```

**Response:**
```json
{
  "exists": true,
  "content": "[2025-01-06T10:00:00] CRITICAL: Unhandled exception\n...",
  "modified": "2025-01-06T10:00:00Z"
}
```

### Run Retention Cleanup

Manually trigger database retention cleanup.

```http
POST /admin/diagnostics/retention
```

**Query Parameters:**
- `message_days` (default: 30): Delete messages older than this
- `inactive_days` (default: 90): Delete conversations inactive for this long
- `max_messages` (default: 500): Max messages per conversation

**Example:**
```bash
curl -X POST \
  -H "X-API-Key: $ADMIN_KEY" \
  "http://localhost:8000/admin/diagnostics/retention?message_days=30"
```

---

## Error Handling

### Common HTTP Status Codes

- **200 OK**: Request successful
- **400 Bad Request**: Invalid request (e.g., duplicate context name)
- **401 Unauthorized**: Missing or invalid API key
- **404 Not Found**: Resource not found (e.g., context doesn't exist)
- **503 Service Unavailable**: Admin API key not configured

### Error Response Format

```json
{
  "detail": "Human-readable error message"
}
```

### Best Practices

**1. Always Check Status Codes:**
```bash
response=$(curl -s -w "\n%{http_code}" \
  -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/contexts)

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

if [ "$http_code" = "200" ]; then
  echo "Success: $body"
else
  echo "Error: $body"
fi
```

**2. Secure API Key Storage:**
```bash
# Use environment variable
export ADMIN_KEY=$(cat .admin_key)

# Or read from secure storage
ADMIN_KEY=$(vault read -field=key secret/admin_api_key)
```

**3. Rate Limiting:**
Admin endpoints are not rate-limited, but avoid excessive polling. Use webhooks or event-driven approaches where possible.

---

## Common Workflows

### Create a New User Workspace

```bash
# 1. Create context
CONTEXT_RESPONSE=$(curl -s -X POST \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "user_alice", "type": "virtual", "default_cwd": "/tmp"}' \
  http://localhost:8000/admin/contexts)

CONTEXT_ID=$(echo $CONTEXT_RESPONSE | jq -r '.context_id')

# 2. (Optional) Set tool permissions
# Use manage_tool_permissions.py script

# 3. User authorizes OAuth
# Guide user to /auth/oauth/authorize endpoint

# 4. Verify setup
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/contexts/$CONTEXT_ID
```

### Revoke User Access

```bash
# 1. Get context ID for user
CONTEXTS=$(curl -s -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/contexts)

CONTEXT_ID=$(echo $CONTEXTS | jq -r '.contexts[] | select(.name=="user_alice") | .id')

# 2. Delete context (deletes everything)
curl -X DELETE \
  -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/contexts/$CONTEXT_ID
```

### Debug OAuth Issues

```bash
# 1. Check OAuth status
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/oauth/status/$CONTEXT_ID

# 2. Check MCP health
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/mcp/health

# 3. Force reconnect
curl -X POST \
  -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/mcp/disconnect/$CONTEXT_ID
```

---

## Security Considerations

### API Key Management

**Generation:**
```bash
# Use cryptographically secure random generator
openssl rand -hex 32  # 64-character hex string
```

**Storage:**
- ✅ Environment variable in production
- ✅ Secrets manager (Vault, AWS Secrets Manager)
- ❌ Never commit to git
- ❌ Never expose in logs

**Rotation:**
```bash
# 1. Generate new key
NEW_KEY=$(openssl rand -hex 32)

# 2. Update .env
echo "AGENT_ADMIN_API_KEY=$NEW_KEY" >> .env

# 3. Restart service
docker-compose restart agent

# 4. Update all admin clients
```

### Network Security

**Production Deployment:**
```nginx
# Restrict admin endpoints to internal network
location /admin/ {
    allow 10.0.0.0/8;      # Internal network
    allow 172.16.0.0/12;    # Docker network
    deny all;

    proxy_pass http://agent:8000;
}
```

### Audit Logging

Consider implementing audit logging for admin actions:

```python
# Custom middleware
@app.middleware("http")
async def audit_admin_requests(request: Request, call_next):
    if request.url.path.startswith("/admin/"):
        logger.info(
            f"Admin request: {request.method} {request.url.path} "
            f"from {request.client.host}"
        )
    return await call_next(request)
```

---

## Rate Limits & Quotas

Currently, admin endpoints have **no rate limiting**. Consider implementing:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.get("/admin/contexts")
@limiter.limit("100/minute")
async def list_contexts(...):
    ...
```

---

## References

- [Multi-Tenant Architecture](./MULTI_TENANT_ARCHITECTURE.md)
- [OAuth Setup Guide](./OAUTH_SETUP.md)
- [Tool Permissions Script](../scripts/manage_tool_permissions.py)
