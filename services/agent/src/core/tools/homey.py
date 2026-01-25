"""Homey smart home control tool."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.providers import get_token_manager_optional
from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# API URLs
ATHOM_API_BASE = "https://api.athom.com"

# Cache configuration
DEVICE_CACHE_TTL_HOURS = 36


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
                    "sync_devices",
                ],
                "description": "Action to perform",
            },
            "homey_id": {
                "type": "string",
                "description": "Homey device ID (required for device/flow actions)",
            },
            "device_id": {
                "type": "string",
                "description": "Device UUID (if known). If not provided, use device_name instead.",
            },
            "device_name": {
                "type": "string",
                "description": (
                    "Device name to search for (e.g., 'Bakom Skärmen'). "
                    "The tool will find the matching device."
                ),
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
            "flow_name": {
                "type": "string",
                "description": "Flow name to search for (alternative to flow_id)",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        """Initialize Homey tool."""
        # Cache: homey_id -> (session_token, homey_url)
        self._session_cache: dict[str, tuple[str, str]] = {}

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

    async def _get_cached_device(
        self,
        context_id: UUID,
        homey_id: str,
        device_name: str,
        db_session: AsyncSession,
    ) -> str | None:
        """Look up device ID from cache by name.

        Args:
            context_id: Context UUID for multi-tenant isolation.
            homey_id: Homey device ID.
            device_name: Device name to search for.
            db_session: Database session.

        Returns:
            Device ID if found in valid cache, None otherwise.
        """
        from core.db.models import HomeyDeviceCache

        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=DEVICE_CACHE_TTL_HOURS)
        query_lower = device_name.lower()

        # Query cache for matching devices
        stmt = select(HomeyDeviceCache).where(
            HomeyDeviceCache.context_id == context_id,
            HomeyDeviceCache.homey_id == homey_id,
            HomeyDeviceCache.cached_at >= cutoff,
        )
        result = await db_session.execute(stmt)
        cached_devices = result.scalars().all()

        if not cached_devices:
            return None

        # Try exact match first
        for device in cached_devices:
            if device.name.lower() == query_lower:
                LOGGER.info(f"Cache hit (exact): '{device.name}' -> {device.device_id}")
                return device.device_id

        # Try partial match
        for device in cached_devices:
            if query_lower in device.name.lower():
                LOGGER.info(f"Cache hit (partial): '{device.name}' -> {device.device_id}")
                return device.device_id

        LOGGER.debug(f"Cache miss for device '{device_name}'")
        return None

    async def _populate_cache(
        self,
        context_id: UUID,
        homey_id: str,
        devices: dict[str, dict[str, Any]],
        db_session: AsyncSession,
    ) -> None:
        """Populate device cache from API response.

        Args:
            context_id: Context UUID.
            homey_id: Homey device ID.
            devices: Device dict from Homey API.
            db_session: Database session.
        """
        from core.db.models import HomeyDeviceCache

        now = datetime.now(UTC).replace(tzinfo=None)

        # Delete existing cache for this homey
        await db_session.execute(
            delete(HomeyDeviceCache).where(
                HomeyDeviceCache.context_id == context_id,
                HomeyDeviceCache.homey_id == homey_id,
            )
        )

        # Insert new cache entries
        for device_id, device in devices.items():
            cache_entry = HomeyDeviceCache(
                context_id=context_id,
                homey_id=homey_id,
                device_id=device_id,
                name=device.get("name", "Unknown"),
                device_class=device.get("class", "other"),
                capabilities=device.get("capabilities", []),
                zone=device.get("zoneName"),
                cached_at=now,
            )
            db_session.add(cache_entry)

        await db_session.flush()
        LOGGER.info(f"Cached {len(devices)} devices for Homey {homey_id}")

    async def run(
        self,
        action: str,
        homey_id: str | None = None,
        device_id: str | None = None,
        device_name: str | None = None,
        capability: str | None = None,
        value: bool | float | str | None = None,
        flow_id: str | None = None,
        flow_name: str | None = None,
        context_id: UUID | None = None,
        session: AsyncSession | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute Homey action.

        Args:
            action: Action to perform.
            homey_id: Homey device ID.
            device_id: Device UUID for device actions.
            device_name: Device name to search for (alternative to device_id).
            capability: Capability name for control_device.
            value: Value to set for control_device.
            flow_id: Flow ID for trigger_flow.
            flow_name: Flow name to search for (alternative to flow_id).
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
                # Resolve device_id from device_name if needed
                resolved_device_id = device_id
                if not resolved_device_id and device_name:
                    resolved_device_id = await self._find_device_by_name(
                        homey_url,
                        session_token,
                        device_name,
                        context_id=context_id,
                        homey_id=homey_id,
                        db_session=session,
                    )
                    if not resolved_device_id:
                        return f"Error: No device found matching '{device_name}'."

                if not resolved_device_id or not capability:
                    return "Error: device_id (or device_name) and capability are required."
                if value is None:
                    return "Error: value is required for control_device."
                return await self._action_control_device(
                    homey_url, session_token, resolved_device_id, capability, value
                )
            elif action == "list_flows":
                return await self._action_list_flows(homey_url, session_token)
            elif action == "trigger_flow":
                resolved_flow_id = flow_id
                if not resolved_flow_id and flow_name:
                    resolved_flow_id = await self._find_flow_by_name(
                        homey_url, session_token, flow_name
                    )
                    if not resolved_flow_id:
                        return f"Error: No flow found matching '{flow_name}'."

                if not resolved_flow_id:
                    return "Error: flow_id or flow_name is required for trigger_flow action."
                return await self._action_trigger_flow(homey_url, session_token, resolved_flow_id)
            elif action == "sync_devices":
                return await self._action_sync_devices(
                    homey_url, session_token, homey_id, context_id, session
                )
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

    async def _find_device_by_name(
        self,
        homey_url: str,
        session_token: str,
        name_query: str,
        context_id: UUID | None = None,
        homey_id: str | None = None,
        db_session: AsyncSession | None = None,
    ) -> str | None:
        """Find a device by name, checking cache first.

        Args:
            homey_url: Homey base URL.
            session_token: Homey session token.
            name_query: Device name to search for.
            context_id: Context UUID for cache lookup.
            homey_id: Homey ID for cache lookup.
            db_session: Database session for cache operations.

        Returns:
            Device ID if found, None otherwise.
        """
        # Try cache first if we have context info
        if context_id and homey_id and db_session:
            cached_id = await self._get_cached_device(context_id, homey_id, name_query, db_session)
            if cached_id:
                return cached_id

        # Cache miss - fetch from API
        devices = await self._homey_request(
            "GET",
            homey_url,
            "/api/manager/devices/device",
            session_token,
        )

        if not devices:
            return None

        # Populate cache if we have context info
        if context_id and homey_id and db_session:
            await self._populate_cache(context_id, homey_id, devices, db_session)

        query_lower = name_query.lower()

        # First try exact match
        for device_id, device in devices.items():
            device_name = device.get("name", "")
            if device_name.lower() == query_lower:
                LOGGER.info(f"API match (exact): '{device_name}' -> {device_id}")
                return device_id

        # Then try partial match
        for device_id, device in devices.items():
            device_name = device.get("name", "")
            if query_lower in device_name.lower():
                LOGGER.info(f"API match (partial): '{device_name}' -> {device_id}")
                return device_id

        LOGGER.warning(f"No device found matching '{name_query}'")
        return None

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
        response = await self._homey_request(
            "PUT",
            homey_url,
            f"/api/manager/devices/device/{device_id}/capability/{capability}/",
            session_token,
            json_body={"value": value},
        )

        LOGGER.info(f"Homey control_device response: {response}")
        return f"Set `{capability}` to `{value}` on device `{device_id}`. Response: {response}"

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

    async def _find_flow_by_name(
        self,
        homey_url: str,
        session_token: str,
        name_query: str,
    ) -> str | None:
        """Find a flow by name (case-insensitive partial match).

        Args:
            homey_url: Homey base URL.
            session_token: Homey session token.
            name_query: Flow name to search for.

        Returns:
            Flow ID if found, None otherwise.
        """
        flows = await self._homey_request(
            "GET",
            homey_url,
            "/api/manager/flow/flow",
            session_token,
        )

        if not flows:
            return None

        query_lower = name_query.lower()

        # Exact match first
        for flow_id, flow in flows.items():
            flow_name = flow.get("name", "")
            if flow_name.lower() == query_lower:
                LOGGER.info(f"Flow match (exact): '{flow_name}' -> {flow_id}")
                return flow_id

        # Partial match
        for flow_id, flow in flows.items():
            flow_name = flow.get("name", "")
            if query_lower in flow_name.lower():
                LOGGER.info(f"Flow match (partial): '{flow_name}' -> {flow_id}")
                return flow_id

        LOGGER.warning(f"No flow found matching '{name_query}'")
        return None

    async def _action_sync_devices(
        self,
        homey_url: str,
        session_token: str,
        homey_id: str | None,
        context_id: UUID | None,
        db_session: AsyncSession | None,
    ) -> str:
        """Manually sync device cache from Homey API.

        Args:
            homey_url: Homey base URL.
            session_token: Homey session token.
            homey_id: Homey device ID.
            context_id: Context UUID.
            db_session: Database session.

        Returns:
            Sync result message.
        """
        if not context_id or not db_session or not homey_id:
            return "Error: Cannot sync devices without context."

        devices = await self._homey_request(
            "GET",
            homey_url,
            "/api/manager/devices/device",
            session_token,
        )

        if not devices:
            return "No devices found on this Homey."

        await self._populate_cache(context_id, homey_id, devices, db_session)
        await db_session.commit()

        return f"Synced {len(devices)} devices to cache."


__all__ = ["HomeyTool"]
