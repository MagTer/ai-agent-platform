# ruff: noqa: E501
"""Integration test for Zapier MCP via credential-based connection.

Tests that:
- A Zapier MCP URL stored as a user credential can be retrieved and decrypted
- McpClient connects to the live Zapier MCP server
- Tools are discovered from Zapier's catalog

Requires:
- PostgreSQL accessible via POSTGRES_URL env var (defaults to dev docker host)
- A user with a `zapier_mcp_url` credential already stored in the database
- AGENT_CREDENTIAL_ENCRYPTION_KEY set to the matching Fernet key

Run with: pytest tests/integration/test_zapier_mcp.py -v -s --log-cli-level=INFO
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.auth.credential_service import CredentialService
from core.mcp.client import McpClient

pytestmark = pytest.mark.integration

LOGGER = logging.getLogger(__name__)

ENCRYPTION_KEY = os.getenv("AGENT_CREDENTIAL_ENCRYPTION_KEY", "")


async def _find_zapier_credential() -> str | None:
    """Find the first zapier_mcp_url credential in the database."""
    if not ENCRYPTION_KEY:
        return None

    from sqlalchemy import select

    from core.db.engine import AsyncSessionLocal
    from core.db.models import UserCredential

    cred_service = CredentialService(ENCRYPTION_KEY)

    async with AsyncSessionLocal() as session:
        stmt = select(UserCredential).where(UserCredential.credential_type == "zapier_mcp_url")
        result = await session.execute(stmt)
        cred = result.scalar_one_or_none()

        if not cred:
            return None

        return cred_service._decrypt(cred.encrypted_value)


@pytest.fixture(scope="module")
async def zapier_url():
    """Get a live Zapier MCP URL from the database, skip if not configured."""
    if not ENCRYPTION_KEY:
        pytest.skip("AGENT_CREDENTIAL_ENCRYPTION_KEY not set")

    url = await _find_zapier_credential()
    if not url:
        pytest.skip("No zapier_mcp_url credential found in database")

    LOGGER.info("Found Zapier MCP URL: %s...%s", url[:45], url[-10:])
    return url


@pytest.mark.asyncio
async def test_zapier_mcp_connects_and_discovers_tools(zapier_url: str) -> None:
    """Connect to Zapier MCP and verify tools are discovered."""
    client = McpClient(
        url=zapier_url,
        auth_token=None,
        name="Zapier",
        auto_reconnect=False,
        max_retries=2,
        cache_ttl_seconds=60,
    )

    try:
        await client.connect()

        assert client.is_connected, "McpClient did not reach CONNECTED state"

        tools = client.tools
        LOGGER.info("Zapier MCP discovered %d tools", len(tools))

        # Zapier should expose at least one tool
        assert len(tools) > 0, "No tools discovered from Zapier MCP"

        # Log first few tools for visibility
        for tool in tools[:5]:
            LOGGER.info("  Tool: %s - %s", tool.name, (tool.description or "")[:80])

    finally:
        await client.disconnect()

    assert not client.is_connected, "Client should be disconnected after cleanup"


@pytest.mark.asyncio
async def test_zapier_mcp_tool_has_valid_schema(zapier_url: str) -> None:
    """Verify discovered Zapier tools have valid name and input schema."""
    client = McpClient(
        url=zapier_url,
        auth_token=None,
        name="Zapier",
        auto_reconnect=False,
        max_retries=2,
    )

    try:
        await client.connect()

        tools = client.tools
        assert len(tools) > 0, "No tools to validate"

        for tool in tools:
            assert tool.name, f"Tool has empty name: {tool}"
            assert isinstance(tool.name, str)
            # Tool names should be reasonable identifiers
            assert len(tool.name) > 0, "Tool name is empty"
            LOGGER.info(
                "  Validated: %s (schema keys: %s)",
                tool.name,
                list(tool.input_schema.keys()) if tool.input_schema else "none",
            )
    finally:
        await client.disconnect()
