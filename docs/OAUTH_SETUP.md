# OAuth 2.0 Setup Guide

This guide explains how to set up and use OAuth 2.0 authentication for external services like Homey.

## Overview

The platform uses OAuth 2.0 Authorization Code Grant with PKCE for secure authentication. Tokens are stored per-user in the database and automatically refreshed.

## Quick Setup (Platform Operator)

### 1. Register OAuth Client

For Homey:
1. Visit https://tools.developer.homey.app/
2. Create a new OAuth client application
3. Set redirect URI to: `https://your-domain.com/auth/oauth/callback`
   - For local development: `http://localhost:8000/auth/oauth/callback`
4. Note your `client_id` and `client_secret`

### 2. Configure Environment

Add to your `.env`:

```bash
# OAuth 2.0 Configuration (Homey)
AGENT_HOMEY_OAUTH_ENABLED=true
AGENT_HOMEY_CLIENT_ID=your_client_id_from_homey
AGENT_HOMEY_CLIENT_SECRET=your_client_secret_from_homey
AGENT_OAUTH_REDIRECT_URI=https://your-domain.com/auth/oauth/callback
```

### 3. Run Database Migration

```bash
cd services/agent
poetry run alembic upgrade head
```

### 4. Restart the Agent

```bash
docker compose restart agent
```

## User Experience

### WebUI OAuth Flow (Recommended)

The platform provides WebUI-optimized endpoints that automatically handle conversation context:

1. **User tries to use a Homey tool** (e.g., "Turn on living room lights")
2. **Tool fails with auth error** → Returns friendly message with guidance
3. **WebUI checks OAuth status** (from chat interface):
   ```bash
   GET /webui/oauth/status/{conversation_id}/homey
   ```
   Returns:
   ```json
   {
     "provider": "homey",
     "is_authorized": false,
     "authorization_url": "https://api.athom.com/oauth2/authorise?...",
     "message": "🔐 Homey authorization required. Click the link to authorize."
   }
   ```

4. **User clicks authorization link** → Logs into Homey → Approves access
5. **Homey redirects back** → Token stored in database
6. **User retries tool** → Works! ✅

**WebUI Endpoints:**
- `GET /webui/oauth/status/{conversation_id}/{provider}` - Check auth status
- `POST /webui/oauth/authorize` - Initiate OAuth (body: `{conversation_id, provider}`)
- `GET /webui/oauth/providers` - List configured providers

### API OAuth Flow (Advanced)

For direct API access or backend integration, use context-based endpoints:

```bash
# Initiate OAuth
curl -X POST http://localhost:8000/auth/oauth/authorize \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "homey",
    "context_id": "<your-context-id>"
  }'

# Returns authorization URL
{
  "authorization_url": "https://api.athom.com/oauth2/authorise?...",
  "state": "random-state",
  "message": "To authorize Homey, please click this link..."
}
```

### Token Management

**Check token status:**
```bash
curl -X POST http://localhost:8000/auth/oauth/status \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "homey",
    "context_id": "<your-context-id>"
  }'
```

**Revoke token:**
```bash
curl -X POST http://localhost:8000/auth/oauth/revoke \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "homey",
    "context_id": "<your-context-id>"
  }'
```

## Architecture

### Components

- **OAuthToken** - Database model storing access/refresh tokens per (context, provider)
- **OAuthState** - Temporary PKCE state storage (10min expiry)
- **TokenManager** - High-level API for multi-provider token management
- **OAuth Client** - PKCE implementation with automatic token refresh
- **API Endpoints** - `/auth/oauth/*` for authorization flow

### Security Features

- ✅ **PKCE** - Prevents authorization code interception
- ✅ **CSRF Protection** - State parameter validation
- ✅ **Automatic Refresh** - Tokens refresh 60s before expiration
- ✅ **Multi-Tenant** - Tokens isolated per context (user/project)
- ✅ **Database Encryption** - PostgreSQL encryption at rest

### Flow Diagram

```
User Request
     ↓
MCP Tool Call → 401 Unauthorized
     ↓
Error Message: "Please authorize"
     ↓
User: "Authorize Homey"
     ↓
POST /auth/oauth/authorize
     ↓
Returns: authorization_url
     ↓
User clicks link → Browser → Homey Login
     ↓
Homey Callback → /auth/oauth/callback
     ↓
Exchange code for tokens → Store in DB
     ↓
MCP Tool Retry → Fetch token from DB → Success! ✅
```

## Future Enhancements

1. **Web UI Integration** - Embed OAuth button in chat interface
2. **Automatic Tool Integration** - Tools auto-trigger OAuth when needed
3. **Additional Providers** - GitHub, Google, Microsoft, etc.
4. **Token Monitoring** - Dashboard showing all active OAuth connections
5. **Webhook Support** - Automatic token revocation on Homey-side disconnect

## Troubleshooting

### Token Not Working

1. Check token exists:
   ```bash
   curl -X POST http://localhost:8000/auth/oauth/status \
     -H "Content-Type: application/json" \
     -d '{"provider": "homey", "context_id": "your-id"}'
   ```

2. Check agent logs:
   ```bash
   docker compose logs agent | grep -i oauth
   ```

3. Revoke and re-authorize:
   ```bash
   # Revoke
   curl -X POST http://localhost:8000/auth/oauth/revoke ...

   # Re-authorize
   curl -X POST http://localhost:8000/auth/oauth/authorize ...
   ```

### Callback Not Working

1. Verify redirect URI matches exactly in:
   - Homey OAuth client settings
   - `.env` file (`AGENT_OAUTH_REDIRECT_URI`)

2. Check Homey OAuth client is active

3. Ensure HTTPS in production (OAuth spec requirement)

## Developer Notes

### Adding New OAuth Providers

1. Add provider config to `TokenManager.__init__()`:
   ```python
   provider_configs["github"] = OAuthProviderConfig(
       provider_name="github",
       authorization_url="https://github.com/login/oauth/authorize",
       token_url="https://github.com/login/oauth/access_token",
       client_id=settings.github_client_id,
       client_secret=settings.github_client_secret,
       scopes="repo user",
       redirect_uri=settings.oauth_redirect_uri,
   )
   ```

2. Add settings to `config.py`

3. Update `.env.template`

4. Register OAuth client with provider

5. Test the flow!

### Testing OAuth Locally

Use ngrok to expose local server for OAuth callbacks:

```bash
ngrok http 8000
# Use ngrok URL as redirect_uri in Homey OAuth settings
```

## Support

For issues or questions:
- Check agent logs: `docker compose logs agent -f`
- Review [OAuth 2.0 RFC 6749](https://datatracker.ietf.org/doc/html/rfc6749)
- Review [PKCE RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)
