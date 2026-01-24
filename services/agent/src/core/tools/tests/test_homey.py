"""Tests for Homey tool."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from core.tools.homey import HomeyTool


@pytest.fixture
def homey_tool() -> HomeyTool:
    """Create a HomeyTool instance for testing."""
    return HomeyTool()


@pytest.fixture
def mock_context_id() -> UUID:
    """Create a mock context ID."""
    return uuid4()


class TestHomeyToolInit:
    """Test HomeyTool initialization."""

    def test_tool_attributes(self, homey_tool: HomeyTool) -> None:
        """Test that tool has correct attributes."""
        assert homey_tool.name == "homey"
        desc_lower = homey_tool.description.lower()
        assert "smart home" in desc_lower or "homey" in desc_lower
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
        mock_context_id: UUID,
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
        mock_context_id: UUID,
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
        mock_context_id: UUID,
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
