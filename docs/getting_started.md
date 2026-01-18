# Getting Started (Local)

## Quick Start

1.  **Copy the environment template and customize**
    ```bash
    cp .env.template .env
    ```
    > **Note**: Set your `OPENROUTER_API_KEY` in `.env` before starting.

2.  **Generate credential encryption key**
    ```bash
    # Generate Fernet encryption key for user credentials
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    # Add to .env
    # AGENT_CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here
    ```
    > **Important**: Store this key securely. Losing it makes all encrypted credentials unrecoverable.

3.  **Install Python dependencies**
    ```bash
    poetry install
    ```

4.  **Launch the stack (idempotent)**
    ```bash
    python -m stack up
    ```

5.  **Check container status**
    ```bash
    python -m stack status
    ```

6.  **Configure Open WebUI for Entra ID (Optional)**

    To enable multi-user support with Entra ID authentication:

    ```bash
    # In Open WebUI .env file
    ENABLE_FORWARD_USER_INFO_HEADERS=true

    # Configure Entra ID OAuth
    OAUTH_PROVIDER=microsoft
    OAUTH_CLIENT_ID=your_entra_id_client_id
    OAUTH_CLIENT_SECRET=your_entra_id_client_secret
    OAUTH_REDIRECT_URI=http://localhost:3000/oauth/callback
    ```

    > **Note**: Without Entra ID, the platform will work but all users will be treated as anonymous.

7.  **Access the UI**
    Open [http://localhost:3000](http://localhost:3000) for Open WebUI. The UI is connected to the agent service, enabling full reasoning traces.
    You can also access the API directly at [http://localhost:8000/v1/agent](http://localhost:8000/v1/agent).

> **Platform Note**: Windows users should run these commands inside a Poetry shell (`poetry shell`). Linux/macOS users can run them directly if poetry is in the path.

## Stack CLI Summary

The platform uses a Python-based orchestration CLI (`stack`) that wraps Docker Compose and provides enhanced status reporting.

| Command | Description |
|---------|-------------|
| `poetry run stack up` | Start or restart the stack in detached mode. |
| `poetry run stack down` | Stop the stack (add `--remove-volumes` to purge data). |
| `poetry run stack status` | Render container status and health checks. |
| `poetry run stack logs [service]` | Tail logs for a specific service (e.g., `openwebui`). |
| `poetry run stack health` | Probe service health endpoints. |

For a full list of automation utilities, referring to the running `poetry run stack --help`.

## Environment Variables Reference

### Required

- `OPENROUTER_API_KEY`: OpenRouter API key for LLM access

### Multi-User Support

- `AGENT_CREDENTIAL_ENCRYPTION_KEY`: Fernet encryption key for user credentials (32 bytes, base64-encoded)
- `ENABLE_FORWARD_USER_INFO_HEADERS`: Set to `true` in Open WebUI to forward user headers

### Entra ID (Open WebUI Configuration)

- `OAUTH_PROVIDER=microsoft`: Enable Microsoft Entra ID OAuth
- `OAUTH_CLIENT_ID`: Entra ID application client ID
- `OAUTH_CLIENT_SECRET`: Entra ID application client secret
- `OAUTH_REDIRECT_URI`: OAuth callback URL (e.g., `http://localhost:3000/oauth/callback`)

### Optional

- `AGENT_DATABASE_URL`: PostgreSQL connection string (default: auto-configured)
- `AGENT_QDRANT_URL`: Qdrant vector database URL (default: `http://qdrant:6333`)
- `AGENT_LOG_LEVEL`: Logging level (default: `INFO`)

## Admin Portal

Access the admin portal at [http://localhost:3000/admin](http://localhost:3000/admin) (requires admin role in Entra ID).

**Admin Portal Features:**
- Context management
- User credential management
- OAuth token management
- MCP client monitoring
- Diagnostics and health checks

**Sections:**
1. Contexts - Manage isolated workspaces
2. Credentials - Manage user credentials (Azure DevOps, GitHub, etc.)
3. OAuth Tokens - View and revoke OAuth integrations
4. MCP Clients - Monitor MCP client connections
5. Diagnostics - System health and traces
6. Azure DevOps - Azure DevOps integration dashboard
7. Price Tracker - Price tracking configuration
8. Dashboards - Analytics and metrics

All admin endpoints require Entra ID authentication with admin role.
