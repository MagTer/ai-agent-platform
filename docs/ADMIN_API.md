# Admin API Reference

The Admin API provides management endpoints for multi-tenant operations, secured with API key authentication.

## Table of Contents

- [Authentication](#authentication)
- [User Management](#user-management)
- [Context Management](#context-management)
- [Credential Management](#credential-management)
- [OAuth Token Management](#oauth-token-management)
- [MCP Client Management](#mcp-client-management)
- [Diagnostics](#diagnostics)
- [Error Handling](#error-handling)

---

## Authentication

All admin endpoints require Entra ID authentication forwarded from Open WebUI with admin role.

### Setup

Configure Open WebUI to forward user information:

```bash
# In Open WebUI .env
ENABLE_FORWARD_USER_INFO_HEADERS=true

# Open WebUI must be configured with Entra ID OAuth
```

### Headers

Open WebUI forwards these headers on every request:

- `X-OpenWebUI-User-Email`: User's email address (normalized to lowercase)
- `X-OpenWebUI-User-Name`: User's display name
- `X-OpenWebUI-User-Id`: Open WebUI internal user ID
- `X-OpenWebUI-User-Role`: User's role (must be "admin" for admin endpoints)

### Authorization

The admin portal requires the `admin` role:

```python
# All admin endpoints check:
if user_role != "admin":
    raise HTTPException(status_code=403, detail="Admin role required")
```

### Error Responses

**401 Unauthorized** - Missing authentication headers:
```json
{
  "detail": "Missing X-OpenWebUI-User-Email header"
}
```

**403 Forbidden** - Non-admin user:
```json
{
  "detail": "Admin role required"
}
```

---

## User Management

Manage users and their context associations.

### Auto-Provisioning

Users are automatically created on first login when Open WebUI forwards authentication headers:

```python
# On every request:
1. Extract email from X-OpenWebUI-User-Email (normalized to lowercase)
2. Check if user exists in database
3. If not exists:
   - Create User record
   - Store name, role from headers
4. Return user for request processing
```

### User Model

Users have the following properties:

- `id` (int): Auto-increment primary key
- `email` (str): Unique email address (case-insensitive, normalized)
- `name` (str): Display name from Entra ID
- `role` (str): User role ("user" or "admin")
- `created_at` (datetime): Account creation timestamp
- `updated_at` (datetime): Last update timestamp

### User-Context Relationship

Users can be associated with multiple contexts via the `user_contexts` junction table:

```sql
CREATE TABLE user_contexts (
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    context_id UUID REFERENCES contexts(id) ON DELETE CASCADE,
    created_at TIMESTAMP,
    PRIMARY KEY (user_id, context_id)
);
```

**Note:** The admin API does not currently expose user management endpoints. Users are managed through Open WebUI's Entra ID integration.

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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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

## Credential Management

Manage per-user encrypted credentials for external integrations.

### Credential Model

User credentials are encrypted using Fernet symmetric encryption:

- `id` (int): Auto-increment primary key
- `user_id` (int): Foreign key to users table
- `credential_type` (str): Type of credential
- `encrypted_value` (bytes): Fernet-encrypted credential
- `created_at` (datetime): Creation timestamp
- `updated_at` (datetime): Last update timestamp

**Unique Constraint:** `(user_id, credential_type)` - one credential per type per user.

### Supported Credential Types

- `azure_devops_pat`: Azure DevOps Personal Access Token
- `github_token`: GitHub Personal Access Token
- `gitlab_token`: GitLab Personal Access Token
- `jira_api_token`: Jira API Token

### Encryption Setup

Credentials are encrypted using the `AGENT_CREDENTIAL_ENCRYPTION_KEY` environment variable:

```bash
# Generate encryption key (32 bytes for Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Add to .env
AGENT_CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here
```

**WARNING:** If you lose this key, all encrypted credentials become unrecoverable.

### List Credentials

Get all credentials for the current user (values are never returned).

```http
GET /admin/credentials/
```

**Example:**
```bash
curl -H "X-OpenWebUI-User-Email: user@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/credentials/
```

**Response:**
```json
{
  "credentials": [
    {
      "id": 1,
      "credential_type": "azure_devops_pat",
      "created_at": "2026-01-18T10:00:00Z",
      "updated_at": "2026-01-18T10:00:00Z"
    },
    {
      "id": 2,
      "credential_type": "github_token",
      "created_at": "2026-01-18T11:00:00Z",
      "updated_at": "2026-01-18T11:00:00Z"
    }
  ],
  "total": 2
}
```

**Note:** The `encrypted_value` is never exposed in API responses for security.

### Create or Update Credential

Create a new credential or update an existing one for the current user.

```http
POST /admin/credentials/
```

**Request Body:**
```json
{
  "credential_type": "azure_devops_pat",
  "value": "your_personal_access_token_here"
}
```

**Example:**
```bash
curl -X POST \
  -H "X-OpenWebUI-User-Email: user@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{
    "credential_type": "azure_devops_pat",
    "value": "abcdef123456"
  }' \
  http://localhost:8000/admin/credentials/
```

**Response (201 Created):**
```json
{
  "success": true,
  "message": "Credential created successfully",
  "credential": {
    "id": 1,
    "credential_type": "azure_devops_pat",
    "created_at": "2026-01-18T10:00:00Z",
    "updated_at": "2026-01-18T10:00:00Z"
  }
}
```

**Response (200 OK - Updated):**
```json
{
  "success": true,
  "message": "Credential updated successfully",
  "credential": {
    "id": 1,
    "credential_type": "azure_devops_pat",
    "created_at": "2026-01-18T10:00:00Z",
    "updated_at": "2026-01-18T12:00:00Z"
  }
}
```

**Error - Missing Encryption Key (500):**
```json
{
  "detail": "Credential encryption not configured. Set AGENT_CREDENTIAL_ENCRYPTION_KEY."
}
```

### Delete Credential

Delete a credential for the current user.

```http
DELETE /admin/credentials/{credential_id}
```

**Example:**
```bash
curl -X DELETE \
  -H "X-OpenWebUI-User-Email: user@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/credentials/1
```

**Response:**
```json
{
  "success": true,
  "message": "Credential deleted successfully",
  "deleted_credential_id": 1
}
```

**Error - Not Found or Unauthorized (404):**
```json
{
  "detail": "Credential not found or unauthorized"
}
```

### Security Considerations

**Encryption:**
- Credentials encrypted at rest using Fernet (AES-128 in CBC mode)
- Encryption key must be stored securely (not in git)
- Key rotation requires re-encrypting all credentials

**Access Control:**
- Users can only access their own credentials
- Admin role required for credential management endpoints
- Credentials scoped per user (not per context)

**Best Practices:**
```bash
# Store encryption key in secrets manager
export AGENT_CREDENTIAL_ENCRYPTION_KEY=$(vault read -field=key secret/credential_key)

# Rotate encryption key (requires migration script)
python scripts/rotate_credential_encryption_key.py --old-key $OLD_KEY --new-key $NEW_KEY
```

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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/diagnostics/summary
```

### Get Crash Log

Get the last crash log (for debugging unhandled exceptions).

```http
GET /admin/diagnostics/crash-log
```

**Example:**
```bash
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
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
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/contexts)

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

if [ "$http_code" = "200" ]; then
  echo "Success: $body"
else
  echo "Error: $body"
fi
```

**2. Authentication via Open WebUI:**
Admin endpoints are designed to be accessed through the Open WebUI admin portal, which automatically forwards authentication headers. Direct API access requires manually setting the headers.

**3. Rate Limiting:**
Admin endpoints are not rate-limited, but avoid excessive polling. Use webhooks or event-driven approaches where possible.

---

## Common Workflows

### Create a New User Workspace

```bash
# 1. Create context
CONTEXT_RESPONSE=$(curl -s -X POST \
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{"name": "user_alice", "type": "virtual", "default_cwd": "/tmp"}' \
  http://localhost:8000/admin/contexts)

CONTEXT_ID=$(echo $CONTEXT_RESPONSE | jq -r '.context_id')

# 2. (Optional) Set tool permissions
# Use manage_tool_permissions.py script

# 3. User authorizes OAuth
# Guide user to /auth/oauth/authorize endpoint

# 4. Verify setup
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/contexts/$CONTEXT_ID
```

### Revoke User Access

```bash
# 1. Get context ID for user
CONTEXTS=$(curl -s -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/contexts)

CONTEXT_ID=$(echo $CONTEXTS | jq -r '.contexts[] | select(.name=="user_alice") | .id')

# 2. Delete context (deletes everything)
curl -X DELETE \
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/contexts/$CONTEXT_ID
```

### Debug OAuth Issues

```bash
# 1. Check OAuth status
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/oauth/status/$CONTEXT_ID

# 2. Check MCP health
curl -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/mcp/health

# 3. Force reconnect
curl -X POST \
  -H "X-OpenWebUI-User-Email: admin@example.com" \
  -H "X-OpenWebUI-User-Role: admin" \
  http://localhost:8000/admin/mcp/disconnect/$CONTEXT_ID
```

---

## Security Considerations

### Entra ID Authentication

**Requirements:**
- Open WebUI must be configured with Entra ID OAuth
- `ENABLE_FORWARD_USER_INFO_HEADERS=true` in Open WebUI
- Users must have admin role in Entra ID

**Security Benefits:**
- Centralized identity management
- No API key rotation needed
- Audit trail via Entra ID logs
- Multi-factor authentication support

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

Admin actions are automatically logged with user identification:

```python
# All admin endpoints log:
# - User email
# - User role
# - Action performed
# - Timestamp
# Example:
logger.info(f"Admin action: {user_email} ({user_role}) - {action}")
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
