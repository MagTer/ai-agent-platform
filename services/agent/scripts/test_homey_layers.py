#!/usr/bin/env python3
"""Test Homey integration at each layer.

Usage:
    # From services/agent directory:
    poetry run python scripts/test_homey_layers.py api --context-id <uuid>
    poetry run python scripts/test_homey_layers.py tool --context-id <uuid>
    poetry run python scripts/test_homey_layers.py control -c <uuid> -d "Name" -v true

Layers:
    api   - Direct HTTP call to Homey API (tests OAuth token and API connectivity)
    tool  - Test HomeyTool.run() directly (tests tool layer)
    control - Test controlling a device (tests full control path)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from uuid import UUID

import typer

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)

app = typer.Typer(help="Test Homey integration at each layer")


async def setup_token_manager() -> None:
    """Set up token manager for testing."""
    import os
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from core.auth.token_manager import TokenManager
    from core.providers import set_token_manager
    from core.runtime.config import get_settings

    db_url = os.getenv(
        "POSTGRES_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5433/agent_db_dev",
    )
    engine = create_async_engine(db_url)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def get_session():
        async with session_factory() as session:
            yield session

    settings = get_settings()
    token_manager = TokenManager(
        session_factory=get_session,
        settings=settings,
    )
    set_token_manager(token_manager)
    LOGGER.info("Token manager configured")


async def get_oauth_token(context_id: UUID) -> str | None:
    """Get OAuth token from database."""
    import os

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from core.db.oauth_models import OAuthToken

    db_url = os.getenv(
        "POSTGRES_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5433/agent_db_dev",
    )
    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.execute(
            select(OAuthToken).where(
                OAuthToken.context_id == context_id,
                OAuthToken.provider == "homey",
            )
        )
        token = result.scalar_one_or_none()

        if token:
            LOGGER.info(f"Found OAuth token for context {context_id}")
            LOGGER.info(f"  Token expires: {token.expires_at}")
            LOGGER.info(f"  Has refresh token: {token.has_refresh_token()}")
            return token.get_access_token()  # Use getter for decryption
        else:
            LOGGER.error(f"No OAuth token found for context {context_id}")
            return None


async def test_api_layer(context_id: UUID) -> None:
    """Test Layer 1: Direct API calls to Homey."""
    import httpx

    LOGGER.info("=" * 60)
    LOGGER.info("LAYER 1: Direct API Test")
    LOGGER.info("=" * 60)

    # Step 1: Get OAuth token from DB
    oauth_token = await get_oauth_token(context_id)
    if not oauth_token:
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 2: Test Athom API - get user info
        LOGGER.info("\n[1/4] Testing Athom API (user info)...")
        try:
            response = await client.get(
                "https://api.athom.com/user/me",
                headers={"Authorization": f"Bearer {oauth_token}"},
            )
            LOGGER.info(f"  Status: {response.status_code}")
            if response.status_code == 200:
                user_data = response.json()
                homeys = user_data.get("homeys", [])
                LOGGER.info(f"  Found {len(homeys)} Homey device(s)")
                for h in homeys:
                    LOGGER.info(f"    - {h.get('name')} (ID: {h.get('_id')})")
                    LOGGER.info(f"      Remote URL: {h.get('remoteUrl')}")
            else:
                LOGGER.error(f"  Error: {response.text[:200]}")
                return
        except Exception as e:
            LOGGER.exception(f"  Failed: {e}")
            return

        if not homeys:
            LOGGER.error("  No Homeys found")
            return

        homey = homeys[0]
        homey_url = homey.get("remoteUrl") or homey.get("remoteForwardedUrl")

        # Step 3: Get delegation token
        LOGGER.info("\n[2/4] Getting delegation token...")
        try:
            response = await client.post(
                "https://api.athom.com/delegation/token",
                params={"audience": "homey"},
                headers={"Authorization": f"Bearer {oauth_token}"},
            )
            LOGGER.info(f"  Status: {response.status_code}")
            if response.status_code == 200:
                delegation_token = response.text.strip().strip('"')
                LOGGER.info(f"  Got delegation token ({len(delegation_token)} chars)")
            else:
                LOGGER.error(f"  Error: {response.text[:200]}")
                return
        except Exception as e:
            LOGGER.exception(f"  Failed: {e}")
            return

        # Step 4: Create Homey session
        LOGGER.info(f"\n[3/4] Creating Homey session at {homey_url}...")
        try:
            response = await client.post(
                f"{homey_url}/api/manager/users/login",
                json={"token": delegation_token},
            )
            LOGGER.info(f"  Status: {response.status_code}")
            if response.status_code == 200:
                session_token = response.text.strip().strip('"')
                LOGGER.info(f"  Got session token ({len(session_token)} chars)")
            else:
                LOGGER.error(f"  Error: {response.text[:200]}")
                return
        except Exception as e:
            LOGGER.exception(f"  Failed: {e}")
            return

        # Step 5: List devices
        LOGGER.info("\n[4/4] Listing devices...")
        try:
            response = await client.get(
                f"{homey_url}/api/manager/devices/device",
                headers={"Authorization": f"Bearer {session_token}"},
            )
            LOGGER.info(f"  Status: {response.status_code}")
            if response.status_code == 200:
                devices = response.json()
                LOGGER.info(f"  Found {len(devices)} device(s)")

                # Show lights specifically
                lights = [
                    (d_id, d)
                    for d_id, d in devices.items()
                    if d.get("class") in ("light", "socket")
                ]
                LOGGER.info(f"\n  Controllable devices ({len(lights)}):")
                for device_id, device in lights[:10]:
                    name = device.get("name", "Unknown")
                    caps = device.get("capabilities", [])
                    cap_vals = device.get("capabilitiesObj", {})
                    onoff_state = cap_vals.get("onoff", {}).get("value", "?")
                    LOGGER.info(f"    - {name}")
                    LOGGER.info(f"      ID: {device_id}")
                    LOGGER.info(f"      Capabilities: {caps}")
                    LOGGER.info(f"      onoff: {onoff_state}")
            else:
                LOGGER.error(f"  Error: {response.text[:200]}")
        except Exception as e:
            LOGGER.exception(f"  Failed: {e}")

    LOGGER.info("\n" + "=" * 60)
    LOGGER.info("Layer 1 test complete")
    LOGGER.info("=" * 60)


async def test_tool_layer(context_id: UUID) -> None:
    """Test Layer 2: HomeyTool directly."""
    LOGGER.info("=" * 60)
    LOGGER.info("LAYER 2: HomeyTool Test")
    LOGGER.info("=" * 60)

    # Set up token manager
    await setup_token_manager()

    from core.tools.homey import HomeyTool

    tool = HomeyTool()

    # Test list_homeys
    LOGGER.info("\n[1/2] Testing list_homeys action...")
    result = await tool.run(
        action="list_homeys",
        context_id=context_id,
    )
    LOGGER.info(f"Result:\n{result}")

    # Test list_devices
    LOGGER.info("\n[2/2] Testing list_devices action...")
    result = await tool.run(
        action="list_devices",
        context_id=context_id,
    )
    LOGGER.info(f"Result:\n{result}")

    LOGGER.info("\n" + "=" * 60)
    LOGGER.info("Layer 2 test complete")
    LOGGER.info("=" * 60)


async def test_control_layer(
    context_id: UUID,
    device_name: str,
    capability: str,
    value: bool | float,
) -> None:
    """Test Layer 3: Control a device."""
    LOGGER.info("=" * 60)
    LOGGER.info("LAYER 3: Control Device Test")
    LOGGER.info("=" * 60)

    # Set up token manager
    await setup_token_manager()

    from core.tools.homey import HomeyTool

    tool = HomeyTool()

    LOGGER.info(f"\nControlling device: {device_name}")
    LOGGER.info(f"Capability: {capability}")
    LOGGER.info(f"Value: {value} (type: {type(value).__name__})")

    result = await tool.run(
        action="control_device",
        device_name=device_name,
        capability=capability,
        value=value,
        context_id=context_id,
    )
    LOGGER.info(f"\nResult:\n{result}")

    LOGGER.info("\n" + "=" * 60)
    LOGGER.info("Layer 3 test complete")
    LOGGER.info("=" * 60)


async def test_raw_control(
    context_id: UUID,
    device_id: str,
    capability: str,
    value: bool | float,
) -> None:
    """Test raw HTTP PUT to control device."""
    import httpx

    LOGGER.info("=" * 60)
    LOGGER.info("RAW CONTROL TEST: Direct HTTP PUT")
    LOGGER.info("=" * 60)

    oauth_token = await get_oauth_token(context_id)
    if not oauth_token:
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get Homey info
        response = await client.get(
            "https://api.athom.com/user/me",
            headers={"Authorization": f"Bearer {oauth_token}"},
        )
        user_data = response.json()
        homey = user_data.get("homeys", [])[0]
        homey_url = homey.get("remoteUrl")

        # Get delegation + session
        response = await client.post(
            "https://api.athom.com/delegation/token",
            params={"audience": "homey"},
            headers={"Authorization": f"Bearer {oauth_token}"},
        )
        delegation_token = response.text.strip().strip('"')

        response = await client.post(
            f"{homey_url}/api/manager/users/login",
            json={"token": delegation_token},
        )
        session_token = response.text.strip().strip('"')

        # Now make the control request
        url = f"{homey_url}/api/manager/devices/device/{device_id}/capability/{capability}"
        LOGGER.info(f"\nPUT {url}")
        LOGGER.info(f"Body: {{'value': {value}}}")

        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {session_token}"},
            json={"value": value},
        )
        LOGGER.info(f"Status: {response.status_code}")
        LOGGER.info(f"Response: {response.text}")


@app.command()
def api(
    context_id: str = typer.Option(..., "--context-id", "-c", help="Context UUID"),
) -> None:
    """Test Layer 1: Direct API calls."""
    asyncio.run(test_api_layer(UUID(context_id)))


@app.command()
def tool(
    context_id: str = typer.Option(..., "--context-id", "-c", help="Context UUID"),
) -> None:
    """Test Layer 2: HomeyTool."""
    asyncio.run(test_tool_layer(UUID(context_id)))


@app.command()
def control(
    context_id: str = typer.Option(..., "--context-id", "-c", help="Context UUID"),
    device: str = typer.Option(..., "--device", "-d", help="Device name"),
    capability: str = typer.Option("onoff", "--capability", help="Capability name"),
    value: str = typer.Option(..., "--value", "-v", help="Value (true/false or number)"),
) -> None:
    """Test Layer 3: Control a device."""
    # Parse value
    if value.lower() == "true":
        parsed_value: bool | float = True
    elif value.lower() == "false":
        parsed_value = False
    else:
        try:
            parsed_value = float(value)
        except ValueError:
            LOGGER.error(f"Invalid value: {value}. Must be true/false or a number.")
            raise typer.Exit(1) from None

    asyncio.run(test_control_layer(UUID(context_id), device, capability, parsed_value))


@app.command()
def raw(
    context_id: str = typer.Option(..., "--context-id", "-c", help="Context UUID"),
    device_id: str = typer.Option(..., "--device-id", help="Device ID (UUID)"),
    capability: str = typer.Option("onoff", "--capability", help="Capability name"),
    value: str = typer.Option(..., "--value", "-v", help="Value (true/false or number)"),
) -> None:
    """Test raw HTTP control (bypass tool layer)."""
    if value.lower() == "true":
        parsed_value: bool | float = True
    elif value.lower() == "false":
        parsed_value = False
    else:
        parsed_value = float(value)

    asyncio.run(test_raw_control(UUID(context_id), device_id, capability, parsed_value))


if __name__ == "__main__":
    app()
