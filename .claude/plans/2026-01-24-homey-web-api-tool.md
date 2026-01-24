# Homey Web API Tool Implementation

**Created:** 2026-01-24
**Status:** Ready for Implementation
**Author:** Architect (Opus)

---

## 1. Feature Overview

### What We're Building

A native Homey Web API tool that allows the agent to control Homey smart home devices directly, bypassing the non-functional MCP server (which only supports browser-based auth).

### Why

Athom's MCP server at `mcp.athom.com` only supports interactive browser-based OAuth (session/cookie-based), NOT programmatic Bearer token authentication. Our OAuth flow successfully obtains Bearer tokens that work against `https://api.athom.com/user/me`, but the MCP server ignores them and redirects to OAuth login.

### Solution

Create a native tool (`HomeyTool`) that uses the Homey Web API directly with our existing OAuth token infrastructure.

---

## 2. Architecture Decisions

### Layer Placement

- **New Tool:** `core/tools/homey.py` (Layer 4 - Core)
- **Uses:** `core/auth/oauth_client.py` for token retrieval
- **Pattern:** Same as `azure_devops.py` (per-user credential lookup via OAuth)

### Why Not a Module?

Tools are simple request-response utilities. Homey control doesn't need:
- Background processing
- State management
- Complex business logic
- Cross-module coordination

Therefore, it belongs in `core/tools/`, not `modules/`.

### Token Flow

```
User Request -> HomeyTool.run()
    -> OAuthClient.get_token(provider="homey", context_id=...)
    -> Returns OAuth Bearer token (auto-refreshes if expired)
    -> HTTP requests to Homey API with Bearer token
```

---

## 3. Homey Web API Reference

Based on research, the Homey Web API uses a delegation token flow:

### Authentication Flow

1. **Get OAuth Token** (we already have this via our OAuth flow)
2. **Get Delegation Token** - Exchange OAuth token for Homey-specific JWT
   ```
   POST https://api.athom.com/delegation/token?audience=homey
   Authorization: Bearer {oauth_token}
   Response: JWT string (delegation token)
   ```

3. **Create Homey Session** - Exchange delegation token for session token
   ```
   POST https://{homey_url}/api/manager/users/login
   Content-Type: application/json
   Body: {"token": "{delegation_token}"}
   Response: Session token string
   ```

4. **API Calls** - Use session token for Homey operations
   ```
   GET/POST/PUT https://{homey_url}/api/...
   Authorization: Bearer {session_token}
   ```

### Key Endpoints

| Action | Method | Endpoint |
|--------|--------|----------|
| Get User Info | GET | `https://api.athom.com/user/me` |
| Get Delegation Token | POST | `https://api.athom.com/delegation/token?audience=homey` |
| Create Session | POST | `https://{homey_url}/api/manager/users/login` |
| List Devices | GET | `https://{homey_url}/api/manager/devices/device` |
| Get Device | GET | `https://{homey_url}/api/manager/devices/device/{device_id}` |
| Set Capability | PUT | `https://{homey_url}/api/manager/devices/device/{device_id}/capability/{capability_id}` |
| List Flows | GET | `https://{homey_url}/api/manager/flow/flow` |
| Trigger Flow | POST | `https://{homey_url}/api/manager/flow/flow/{flow_id}/trigger` |

### Homey URL Discovery

The Homey URL is obtained from user info:
```json
{
  "email": "user@example.com",
  "homeys": [
    {
      "_id": "65857ca0cf2ded82d2a5332c",
      "name": "Homey Pro",
      "remoteUrl": "https://65857ca0cf2ded82d2a5332c.homey.athom-prod-euwest1-001.homeypro.net"
    }
  ]
}
```

---

## 4. Implementation Roadmap

### Step 1: Create Homey Tool

**Engineer tasks:**
- Create `services/agent/src/core/tools/homey.py`
- Implement `HomeyTool` class with actions: `list_devices`, `get_device`, `control_device`, `list_flows`, `trigger_flow`

**QA tasks (after Engineer completes):**
- Run `stack check`
- Fix any linting issues

**File:** `services/agent/src/core/tools/homey.py` (CREATE)

```python
"""Homey smart home control tool."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.providers import get_token_manager_optional
from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# API URLs
ATHOM_API_BASE = "https://api.athom.com"


class HomeyTool(Tool):
    """Control Homey smart home devices via Web API.

    Actions:
    - list_homeys: Get all Homey devices linked to user account
    - list_devices: Get all devices on a Homey
    - get_device: Get device details by ID
    - control_device: Set device capability (on/off, dim, etc.)
    - list_flows: Get all flows on a Homey
    - trigger_flow: Start a flow by ID
    """

    name = "homey"
    description = (
        "Control Homey smart home devices. "
        "Actions: list_homeys, list_devices, get_device, control_device, list_flows, trigger_flow. "
        "Requires Homey OAuth authorization."
    )
    category = "smart_home"
    activity_hint = {"action": "Homey: {action}"}

    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_homeys",
                    "list_devices",
                    "get_device",
                    "control_device",
                    "list_flows",
                    "trigger_flow",
                ],
                "description": "Action to perform",
            },
            "homey_id": {
                "type": "string",
                "description": "Homey device ID (required for device/flow actions)",
            },
            "device_id": {
                "type": "string",
                "description": "Device ID (required for get_device, control_device)",
            },
            "capability": {
                "type": "string",
                "description": "Capability to set (e.g., 'onoff', 'dim', 'target_temperature')",
            },
            "value": {
                "type": ["boolean", "number", "string"],
                "description": "Value to set (e.g., true for on, 0.5 for 50% dim)",
            },
            "flow_id": {
                "type": "string",
                "description": "Flow ID (required for trigger_flow)",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        """Initialize Homey tool."""
        self._session_cache: dict[str, tuple[str, str]] = {}  # homey_id -> (session_token, homey_url)

    async def _get_oauth_token(
        self,
        context_id: UUID | None,
        session: AsyncSession | None,
    ) -> str | None:
        """Get OAuth token for Homey provider.

        Args:
            context_id: Context UUID for token lookup.
            session: Database session (unused, kept for interface consistency).

        Returns:
            OAuth access token or None if not available.
        """
        if not context_id:
            LOGGER.warning("Homey: No context_id provided")
            return None

        token_manager = get_token_manager_optional()
        if not token_manager:
            LOGGER.warning("Homey: Token manager not available")
            return None

        token = await token_manager.get_token(
            provider="homey",
            context_id=context_id,
        )
        return token

    async def _get_delegation_token(self, oauth_token: str) -> str:
        """Exchange OAuth token for Homey delegation token.

        Args:
            oauth_token: OAuth access token.

        Returns:
            Delegation token (JWT).

        Raises:
            httpx.HTTPStatusError: If request fails.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ATHOM_API_BASE}/delegation/token",
                params={"audience": "homey"},
                headers={"Authorization": f"Bearer {oauth_token}"},
            )
            response.raise_for_status()
            return response.text.strip().strip('"')  # Response is a raw JWT string

    async def _get_homey_session(
        self,
        homey_url: str,
        delegation_token: str,
    ) -> str:
        """Create Homey session with delegation token.

        Args:
            homey_url: Homey remote URL.
            delegation_token: Delegation JWT token.

        Returns:
            Session token for Homey API.

        Raises:
            httpx.HTTPStatusError: If request fails.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{homey_url}/api/manager/users/login",
                json={"token": delegation_token},
            )
            response.raise_for_status()
            return response.text.strip().strip('"')

    async def _get_user_homeys(self, oauth_token: str) -> list[dict[str, Any]]:
        """Get list of Homey devices for authenticated user.

        Args:
            oauth_token: OAuth access token.

        Returns:
            List of Homey device info dicts.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{ATHOM_API_BASE}/user/me",
                headers={"Authorization": f"Bearer {oauth_token}"},
            )
            response.raise_for_status()
            user_data = response.json()
            return user_data.get("homeys", [])

    async def _ensure_session(
        self,
        homey_id: str,
        oauth_token: str,
    ) -> tuple[str, str]:
        """Ensure we have a valid session for the Homey.

        Args:
            homey_id: Homey device ID.
            oauth_token: OAuth access token.

        Returns:
            Tuple of (session_token, homey_url).
        """
        # Check cache
        if homey_id in self._session_cache:
            return self._session_cache[homey_id]

        # Get Homey URL from user info
        homeys = await self._get_user_homeys(oauth_token)
        homey_info = next((h for h in homeys if h.get("_id") == homey_id), None)

        if not homey_info:
            raise ValueError(f"Homey '{homey_id}' not found in user account")

        homey_url = homey_info.get("remoteUrl") or homey_info.get("remoteForwardedUrl")
        if not homey_url:
            raise ValueError(f"Homey '{homey_id}' has no remote URL configured")

        # Get delegation token and session
        delegation_token = await self._get_delegation_token(oauth_token)
        session_token = await self._get_homey_session(homey_url, delegation_token)

        # Cache session
        self._session_cache[homey_id] = (session_token, homey_url)
        return session_token, homey_url

    async def _homey_request(
        self,
        method: str,
        homey_url: str,
        path: str,
        session_token: str,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Make authenticated request to Homey API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            homey_url: Homey base URL.
            path: API path (e.g., "/api/manager/devices/device").
            session_token: Homey session token.
            json_body: Optional JSON body for POST/PUT.

        Returns:
            Parsed JSON response.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method,
                url=f"{homey_url}{path}",
                headers={"Authorization": f"Bearer {session_token}"},
                json=json_body,
            )
            response.raise_for_status()
            return response.json()

    async def run(
        self,
        action: str,
        homey_id: str | None = None,
        device_id: str | None = None,
        capability: str | None = None,
        value: bool | float | str | None = None,
        flow_id: str | None = None,
        context_id: UUID | None = None,
        session: AsyncSession | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute Homey action.

        Args:
            action: Action to perform.
            homey_id: Homey device ID.
            device_id: Device ID for device actions.
            capability: Capability name for control_device.
            value: Value to set for control_device.
            flow_id: Flow ID for trigger_flow.
            context_id: Context UUID (injected by agent).
            session: Database session (injected by agent).
            **kwargs: Additional arguments (ignored).

        Returns:
            Action result as formatted string.
        """
        # Get OAuth token
        oauth_token = await self._get_oauth_token(context_id, session)
        if not oauth_token:
            return (
                "Homey is not authorized. Please authorize Homey access first.\n\n"
                "You can do this via the Admin Portal -> OAuth -> Connect Homey."
            )

        try:
            if action == "list_homeys":
                return await self._action_list_homeys(oauth_token)

            # All other actions require homey_id
            if not homey_id:
                # Try to get first Homey
                homeys = await self._get_user_homeys(oauth_token)
                if not homeys:
                    return "No Homey devices found in your account."
                homey_id = homeys[0]["_id"]
                LOGGER.info(f"Using default Homey: {homey_id}")

            session_token, homey_url = await self._ensure_session(homey_id, oauth_token)

            if action == "list_devices":
                return await self._action_list_devices(homey_url, session_token)
            elif action == "get_device":
                if not device_id:
                    return "Error: device_id is required for get_device action."
                return await self._action_get_device(homey_url, session_token, device_id)
            elif action == "control_device":
                if not device_id or not capability:
                    return "Error: device_id and capability are required for control_device."
                if value is None:
                    return "Error: value is required for control_device."
                return await self._action_control_device(
                    homey_url, session_token, device_id, capability, value
                )
            elif action == "list_flows":
                return await self._action_list_flows(homey_url, session_token)
            elif action == "trigger_flow":
                if not flow_id:
                    return "Error: flow_id is required for trigger_flow action."
                return await self._action_trigger_flow(homey_url, session_token, flow_id)
            else:
                return f"Unknown action: {action}"

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                # Clear session cache on auth error
                if homey_id and homey_id in self._session_cache:
                    del self._session_cache[homey_id]
                return (
                    "Homey authorization expired. Please re-authorize via "
                    "Admin Portal -> OAuth -> Connect Homey."
                )
            LOGGER.error(f"Homey API error: {e}")
            return f"Homey API error: {e.response.status_code} - {e.response.text[:200]}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            LOGGER.exception("Homey tool error")
            return f"Error: {e}"

    async def _action_list_homeys(self, oauth_token: str) -> str:
        """List all Homey devices for the user."""
        homeys = await self._get_user_homeys(oauth_token)

        if not homeys:
            return "No Homey devices found in your account."

        lines = ["### Your Homey Devices\n"]
        for h in homeys:
            name = h.get("name", "Unknown")
            homey_id = h.get("_id", "unknown")
            platform = h.get("platform", "unknown")
            lines.append(f"- **{name}** (ID: `{homey_id}`, Platform: {platform})")
        return "\n".join(lines)

    async def _action_list_devices(
        self,
        homey_url: str,
        session_token: str,
    ) -> str:
        """List all devices on a Homey."""
        devices = await self._homey_request(
            "GET",
            homey_url,
            "/api/manager/devices/device",
            session_token,
        )

        if not devices:
            return "No devices found on this Homey."

        # Group by class
        by_class: dict[str, list[dict[str, Any]]] = {}
        for device_id, device in devices.items():
            device_class = device.get("class", "other")
            if device_class not in by_class:
                by_class[device_class] = []
            by_class[device_class].append({"id": device_id, **device})

        lines = [f"### Devices ({len(devices)} total)\n"]
        for device_class, class_devices in sorted(by_class.items()):
            lines.append(f"**{device_class.title()}** ({len(class_devices)})")
            for d in class_devices:
                name = d.get("name", "Unknown")
                device_id = d.get("id")
                caps = ", ".join(d.get("capabilities", [])[:3])
                if len(d.get("capabilities", [])) > 3:
                    caps += "..."
                lines.append(f"  - {name} (`{device_id}`) - Capabilities: {caps}")
            lines.append("")

        return "\n".join(lines)

    async def _action_get_device(
        self,
        homey_url: str,
        session_token: str,
        device_id: str,
    ) -> str:
        """Get detailed device info."""
        device = await self._homey_request(
            "GET",
            homey_url,
            f"/api/manager/devices/device/{device_id}",
            session_token,
        )

        name = device.get("name", "Unknown")
        device_class = device.get("class", "unknown")
        zone = device.get("zoneName", "Unknown")
        capabilities = device.get("capabilities", [])
        cap_values = device.get("capabilitiesObj", {})

        lines = [
            f"### {name}",
            f"- **Class:** {device_class}",
            f"- **Zone:** {zone}",
            f"- **ID:** `{device_id}`",
            "",
            "**Capabilities:**",
        ]

        for cap in capabilities:
            cap_info = cap_values.get(cap, {})
            value = cap_info.get("value")
            title = cap_info.get("title", cap)
            if value is not None:
                lines.append(f"  - {title}: `{value}`")
            else:
                lines.append(f"  - {title}")

        return "\n".join(lines)

    async def _action_control_device(
        self,
        homey_url: str,
        session_token: str,
        device_id: str,
        capability: str,
        value: bool | float | str,
    ) -> str:
        """Set a device capability value."""
        await self._homey_request(
            "PUT",
            homey_url,
            f"/api/manager/devices/device/{device_id}/capability/{capability}",
            session_token,
            json_body={"value": value},
        )

        return f"Set `{capability}` to `{value}` on device `{device_id}`."

    async def _action_list_flows(
        self,
        homey_url: str,
        session_token: str,
    ) -> str:
        """List all flows on a Homey."""
        flows = await self._homey_request(
            "GET",
            homey_url,
            "/api/manager/flow/flow",
            session_token,
        )

        if not flows:
            return "No flows found on this Homey."

        # Sort by folder
        by_folder: dict[str, list[dict[str, Any]]] = {"(No Folder)": []}
        for flow_id, flow in flows.items():
            folder = flow.get("folder") or "(No Folder)"
            if folder not in by_folder:
                by_folder[folder] = []
            by_folder[folder].append({"id": flow_id, **flow})

        lines = [f"### Flows ({len(flows)} total)\n"]
        for folder, folder_flows in sorted(by_folder.items()):
            if folder_flows:
                lines.append(f"**{folder}**")
                for f in folder_flows:
                    name = f.get("name", "Unnamed")
                    flow_id = f.get("id")
                    enabled = "Enabled" if f.get("enabled", True) else "Disabled"
                    lines.append(f"  - {name} (`{flow_id}`) - {enabled}")
                lines.append("")

        return "\n".join(lines)

    async def _action_trigger_flow(
        self,
        homey_url: str,
        session_token: str,
        flow_id: str,
    ) -> str:
        """Trigger a flow."""
        await self._homey_request(
            "POST",
            homey_url,
            f"/api/manager/flow/flow/{flow_id}/trigger",
            session_token,
        )

        return f"Flow `{flow_id}` triggered successfully."


__all__ = ["HomeyTool"]
```

---

### Step 2: Register Homey Tool in tools.yaml

**Engineer tasks:**
- Add Homey tool entry to `config/tools.yaml`

**QA tasks (after Engineer completes):**
- Run `stack check`

**File:** `services/agent/config/tools.yaml` (MODIFY)

Add at the end of the file:

```yaml
- name: homey
  type: core.tools.homey.HomeyTool
  enabled: true
  description: "Control Homey smart home devices (lights, sensors, flows)"
```

---

### Step 3: Add Optional Token Manager Getter

**Engineer tasks:**
- Add `get_token_manager_optional()` function to `core/providers.py`

**File:** `services/agent/src/core/providers.py` (MODIFY)

The `get_token_manager()` function already exists but raises `ProviderError` if not configured.
Add an optional variant (similar to `get_email_service_optional`):

After line 116 (after `get_token_manager()`), add:

```python
def get_token_manager_optional() -> TokenManager | None:
    """Get the token manager if configured, or None.

    Use this when OAuth is optional (e.g., tools that can work without auth).
    """
    return _token_manager
```

Update `__all__` to include `"get_token_manager_optional"`.

---

### Step 4: Clean Up Homey MCP Configuration

**Engineer tasks:**
- Remove Homey MCP client creation from `client_pool.py`
- Remove `homey_mcp_url` and `homey_api_token` from `config.py`
- Keep OAuth settings (`homey_oauth_enabled`, `homey_client_id`, etc.)

**QA tasks (after Engineer completes):**
- Run `stack check`
- Verify no broken imports

#### 4a. Modify `services/agent/src/core/mcp/client_pool.py`

Remove the Homey MCP client creation block (lines 126-145):

```python
# REMOVE THIS ENTIRE BLOCK:
                # Homey MCP
                if provider == "homey" and self._settings.homey_mcp_url:
                    try:
                        client = McpClient(
                            url=str(self._settings.homey_mcp_url),
                            context_id=context_id,
                            oauth_provider="homey",
                            name="Homey",
                            auto_reconnect=True,
                            max_retries=3,
                            cache_ttl_seconds=300,  # 5 minute cache
                        )
                        await client.connect()
                        clients.append(client)
                        LOGGER.info(
                            f"Connected Homey MCP for context {context_id} "
                            f"(discovered {len(client.tools)} tools)"
                        )
                    except Exception as e:
                        LOGGER.error(f"Failed to connect Homey MCP for context {context_id}: {e}")
```

#### 4b. Modify `services/agent/src/core/core/config.py`

Remove these lines (around lines 20, 98-104):

```python
# REMOVE line 20:
DEFAULT_HOMEY_MCP_URL: HttpUrl = cast(HttpUrl, "https://mcp.athom.com/sse")

# REMOVE lines 98-104:
    homey_mcp_url: HttpUrl | None = Field(
        default=None,
        description="URL for the Homey Model Context Protocol (MCP) server.",
    )
    homey_api_token: str | None = Field(
        default=None, description="API token for authenticating with Homey MCP."
    )
```

**KEEP these OAuth settings (lines 106-130):**
```python
    # OAuth 2.0 Configuration (Homey)
    homey_oauth_enabled: bool = Field(...)
    homey_authorization_url: HttpUrl = Field(...)
    homey_token_url: HttpUrl = Field(...)
    homey_client_id: str | None = Field(...)
    homey_client_secret: str | None = Field(...)
    oauth_redirect_uri: HttpUrl | None = Field(...)
```

---

### Step 5: Verify TokenManager Initialization

**Engineer tasks:**
- Verify `TokenManager` is initialized at app startup (should already exist)
- No changes needed if `set_token_manager()` is called in app startup

**File:** `services/agent/src/interfaces/http/app.py` (VERIFY ONLY)

The tool uses `get_token_manager_optional()` which returns `None` if not configured.
This allows graceful degradation with a helpful error message.

---

### Step 6: Create Unit Tests

**Engineer tasks:**
- Create test file for Homey tool

**QA tasks (after Engineer completes):**
- Run tests: `stack test`

**File:** `services/agent/src/core/tools/tests/test_homey.py` (CREATE)

```python
"""Tests for Homey tool."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from core.tools.homey import HomeyTool


@pytest.fixture
def homey_tool() -> HomeyTool:
    """Create a HomeyTool instance for testing."""
    return HomeyTool()


@pytest.fixture
def mock_context_id():
    """Create a mock context ID."""
    return uuid4()


class TestHomeyToolInit:
    """Test HomeyTool initialization."""

    def test_tool_attributes(self, homey_tool: HomeyTool) -> None:
        """Test that tool has correct attributes."""
        assert homey_tool.name == "homey"
        assert "smart home" in homey_tool.description.lower() or "homey" in homey_tool.description.lower()
        assert homey_tool.category == "smart_home"

    def test_parameters_schema(self, homey_tool: HomeyTool) -> None:
        """Test that parameters schema is valid."""
        params = homey_tool.parameters
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert params["required"] == ["action"]


class TestHomeyToolNoAuth:
    """Test HomeyTool behavior without authentication."""

    @pytest.mark.asyncio
    async def test_no_context_id(self, homey_tool: HomeyTool) -> None:
        """Test that tool returns error without context_id."""
        result = await homey_tool.run(action="list_homeys")
        assert "not authorized" in result.lower() or "authorize" in result.lower()

    @pytest.mark.asyncio
    async def test_no_token_manager(
        self,
        homey_tool: HomeyTool,
        mock_context_id,
    ) -> None:
        """Test that tool returns error without token manager."""
        with patch("core.tools.homey.get_token_manager_optional", return_value=None):
            result = await homey_tool.run(
                action="list_homeys",
                context_id=mock_context_id,
            )
            assert "not authorized" in result.lower() or "authorize" in result.lower()


class TestHomeyToolActions:
    """Test HomeyTool actions with mocked API."""

    @pytest.mark.asyncio
    async def test_list_homeys(
        self,
        homey_tool: HomeyTool,
        mock_context_id,
    ) -> None:
        """Test listing Homey devices."""
        mock_token_manager = MagicMock()
        mock_token_manager.get_token = AsyncMock(return_value="mock_token")

        mock_homeys = [
            {"_id": "abc123", "name": "Homey Pro", "platform": "pro"},
        ]

        with patch("core.tools.homey.get_token_manager_optional", return_value=mock_token_manager):
            with patch.object(
                homey_tool,
                "_get_user_homeys",
                AsyncMock(return_value=mock_homeys),
            ):
                result = await homey_tool.run(
                    action="list_homeys",
                    context_id=mock_context_id,
                )

        assert "Homey Pro" in result
        assert "abc123" in result

    @pytest.mark.asyncio
    async def test_control_device_requires_params(
        self,
        homey_tool: HomeyTool,
        mock_context_id,
    ) -> None:
        """Test that control_device requires device_id and capability."""
        mock_token_manager = MagicMock()
        mock_token_manager.get_token = AsyncMock(return_value="mock_token")

        mock_homeys = [
            {"_id": "abc123", "name": "Homey Pro", "remoteUrl": "https://example.com"},
        ]

        with patch("core.tools.homey.get_token_manager_optional", return_value=mock_token_manager):
            with patch.object(
                homey_tool,
                "_get_user_homeys",
                AsyncMock(return_value=mock_homeys),
            ):
                with patch.object(
                    homey_tool,
                    "_get_delegation_token",
                    AsyncMock(return_value="delegation"),
                ):
                    with patch.object(
                        homey_tool,
                        "_get_homey_session",
                        AsyncMock(return_value="session"),
                    ):
                        result = await homey_tool.run(
                            action="control_device",
                            context_id=mock_context_id,
                        )

        assert "error" in result.lower() or "required" in result.lower()
```

---

## 5. Configuration Changes

### Environment Variables to Remove

These are no longer needed (Homey MCP doesn't work):

```bash
# REMOVE from .env:
AGENT_HOMEY_MCP_URL=...
AGENT_HOMEY_API_TOKEN=...
```

### Environment Variables to Keep

These are still needed for OAuth:

```bash
# KEEP in .env:
AGENT_HOMEY_OAUTH_ENABLED=true
AGENT_HOMEY_CLIENT_ID=your_client_id
AGENT_HOMEY_CLIENT_SECRET=your_client_secret
AGENT_HOMEY_AUTHORIZATION_URL=https://api.athom.com/oauth2/authorise
AGENT_HOMEY_TOKEN_URL=https://api.athom.com/oauth2/token
AGENT_OAUTH_REDIRECT_URI=https://your-app.com/auth/oauth/callback
```

---

## 6. Testing Strategy

### Unit Tests

- Test tool initialization and parameters
- Test error handling without auth
- Test action routing
- Mock HTTP responses for API calls

### Integration Tests (Manual)

1. **OAuth Flow:**
   - Go to Admin Portal -> OAuth -> Connect Homey
   - Complete OAuth authorization
   - Verify token stored in database

2. **Tool Usage:**
   - Ask agent: "List my Homey devices"
   - Ask agent: "Turn on the living room lights"
   - Ask agent: "Trigger the 'Good Night' flow"

3. **Error Handling:**
   - Revoke OAuth token, verify helpful error message
   - Use invalid device ID, verify error handling

---

## 7. Quality Checks

After each step, run:

```bash
cd /home/magnus/dev/ai-agent-platform
./stack check
```

This runs:
1. **Ruff** - Linting
2. **Black** - Formatting
3. **Mypy** - Type checking
4. **Pytest** - Unit tests

---

## 8. Security Considerations

### Token Security

- OAuth tokens stored encrypted in database (existing infrastructure)
- Session tokens cached in memory only (cleared on 401)
- No tokens logged (LOGGER doesn't output sensitive data)

### Input Validation

- Action enum restricts valid actions
- Device/flow IDs validated by Homey API
- Capability values validated by Homey API

### Authorization

- Tool requires valid OAuth token (context-scoped)
- Each Homey session requires delegation token exchange
- 401 responses clear cached sessions

### SSRF Prevention

- Homey URLs obtained from authenticated API response
- No user-controlled URLs in requests

---

## 9. Success Criteria

1. `./stack check` passes with no errors
2. Unit tests pass for new Homey tool
3. Manual testing confirms:
   - OAuth flow works
   - Device listing works
   - Device control works
   - Flow triggering works
4. MCP cleanup complete (no references to `homey_mcp_url`)

---

## 10. Agent Delegation

### Engineer (Sonnet) - Implementation

- Create `homey.py` tool file
- Modify `tools.yaml`
- Modify `config.py` to remove MCP settings
- Modify `client_pool.py` to remove Homey MCP
- Add `get_token_manager` provider if needed
- Create unit tests

### QA (Haiku - 12x cheaper) - Quality Assurance

- Run `stack check` after each step
- Fix auto-fixable lint issues
- Report test results
- Escalate complex Mypy errors to Engineer

### Implementation Order

1. **Engineer:** Create `homey.py` (Step 1)
2. **QA:** Run quality check
3. **Engineer:** Register in `tools.yaml` (Step 2)
4. **QA:** Run quality check
5. **Engineer:** Add provider function (Step 3)
6. **QA:** Run quality check
7. **Engineer:** Clean up MCP config (Step 4a, 4b)
8. **QA:** Run quality check
9. **Engineer:** Create unit tests (Step 6)
10. **QA:** Run full test suite, report results

---

## 11. Files Summary

| File | Action | Description |
|------|--------|-------------|
| `services/agent/src/core/tools/homey.py` | CREATE | New Homey Web API tool |
| `services/agent/config/tools.yaml` | MODIFY | Add Homey tool registration |
| `services/agent/src/core/providers.py` | MODIFY | Ensure `get_token_manager` exists |
| `services/agent/src/core/mcp/client_pool.py` | MODIFY | Remove Homey MCP client |
| `services/agent/src/core/core/config.py` | MODIFY | Remove `homey_mcp_url`, `homey_api_token` |
| `services/agent/src/core/tools/tests/test_homey.py` | CREATE | Unit tests |

---

## 12. Rollback Plan

If issues occur:

1. Revert changes to `config.py` and `client_pool.py`
2. Remove `homey.py` tool
3. Remove tool registration from `tools.yaml`
4. OAuth infrastructure remains unchanged

---

## References

- [Homey Web API Documentation](https://api.developer.homey.app/)
- [Homey OAuth HTTP Specification](https://api.developer.homey.app/http-and-socket.io/http-specification)
- [homey-api npm package](https://athombv.github.io/node-homey-api/)
