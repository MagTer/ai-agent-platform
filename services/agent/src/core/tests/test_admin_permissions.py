"""Tests for admin permissions endpoints."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI

MOCK_TOOLS_YAML = [
    {
        "name": "calculator",
        "type": "core.tools.calculator.CalculatorTool",
        "description": "Basic calculator",
    },
    {
        "name": "web_search",
        "type": "core.tools.web_search.WebSearchTool",
        "description": "Search the web",
    },
    {
        "name": "web_fetch",
        "type": "core.tools.web_fetch.WebFetchTool",
        "description": "Fetch web pages",
    },
]


def _create_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the permissions router for testing."""
    from fastapi import FastAPI

    from interfaces.http.admin_permissions import router

    app = FastAPI()
    app.include_router(router)
    return app


def _make_admin_dependency_override() -> object:
    """Create a mock admin dependency override."""
    from interfaces.http.admin_auth import AdminUser

    class MockIdentity:
        email = "admin@test.com"
        name = "Admin"
        role = "admin"
        openwebui_id = None

    class MockDbUser:
        id = uuid4()
        email = "admin@test.com"
        display_name = "Admin"
        role = "admin"
        is_active = True

    mock_admin = AdminUser(identity=MockIdentity(), db_user=MockDbUser())  # type: ignore[arg-type]
    return mock_admin


@pytest.fixture
def mock_settings() -> object:
    """Create mock settings with a tools_config_path."""
    from pathlib import Path

    from core.runtime.config import Settings

    return Settings(
        litellm_model="mock-model",
        litellm_api_key="mock-key",
        litellm_api_base="http://mock-url",
        tools_config_path=Path("config/tools.yaml"),
    )


class TestLoadAvailableTools:
    """Test the _load_available_tools helper."""

    def test_load_from_yaml(self, mock_settings: object) -> None:
        """Test loading tools from tools.yaml."""
        from interfaces.http.admin_permissions import _load_available_tools

        with patch(
            "interfaces.http.admin_permissions.yaml.safe_load",
            return_value=MOCK_TOOLS_YAML,
        ):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.read_text", return_value="mock"):
                    tools = _load_available_tools(mock_settings)  # type: ignore[arg-type]

        assert len(tools) == 3
        # Should be sorted by name
        assert tools[0]["name"] == "calculator"
        assert tools[1]["name"] == "web_fetch"
        assert tools[2]["name"] == "web_search"

    def test_load_missing_file(self, mock_settings: object) -> None:
        """Test loading when tools.yaml doesn't exist."""
        from interfaces.http.admin_permissions import _load_available_tools

        with patch("pathlib.Path.exists", return_value=False):
            tools = _load_available_tools(mock_settings)  # type: ignore[arg-type]

        assert tools == []

    def test_load_invalid_yaml(self, mock_settings: object) -> None:
        """Test loading when tools.yaml has invalid content."""
        from interfaces.http.admin_permissions import _load_available_tools

        with patch(
            "interfaces.http.admin_permissions.yaml.safe_load",
            return_value="not a list",
        ):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.read_text", return_value="mock"):
                    tools = _load_available_tools(mock_settings)  # type: ignore[arg-type]

        assert tools == []


class TestPermissionSemantics:
    """Test the permission model semantics.

    Key rule: When NO permissions exist -> all tools allowed.
    When ANY permissions exist -> only explicitly allowed tools are permitted.
    """

    def test_default_state_allows_all(self) -> None:
        """Verify that with no ToolPermission rows, all tools are allowed."""
        # This is tested via ServiceFactory, but we verify the concept here
        from core.tools.registry import ToolRegistry

        registry = ToolRegistry()

        class FakeTool:
            name = "test_tool"
            description = "Test"

        registry.register(FakeTool())  # type: ignore[arg-type]

        # No permissions applied -> tool remains
        assert "test_tool" in registry.available()

    def test_explicit_deny_removes_tool(self) -> None:
        """Verify that explicit deny removes tool from registry."""
        from core.tools.registry import ToolRegistry

        registry = ToolRegistry()

        class FakeTool:
            name = "test_tool"
            description = "Test"

        registry.register(FakeTool())  # type: ignore[arg-type]

        # Apply permissions: test_tool denied
        registry.filter_by_permissions({"test_tool": False})
        assert "test_tool" not in registry.available()

    def test_explicit_allow_keeps_tool(self) -> None:
        """Verify that explicit allow keeps tool in registry."""
        from core.tools.registry import ToolRegistry

        registry = ToolRegistry()

        class FakeTool:
            name = "test_tool"
            description = "Test"

        registry.register(FakeTool())  # type: ignore[arg-type]

        # Apply permissions: test_tool allowed
        registry.filter_by_permissions({"test_tool": True})
        assert "test_tool" in registry.available()
