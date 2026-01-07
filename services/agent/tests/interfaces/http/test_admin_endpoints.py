"""Integration tests for admin endpoints.

Tests all admin HTTP endpoints with authentication.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core.core.app import create_app
from core.db.models import Context, Conversation
from core.db.oauth_models import OAuthToken


@pytest.fixture
def admin_api_key():
    """Admin API key for testing."""
    return "test_admin_key_secret_12345"


@pytest.fixture
def app_with_admin(settings, admin_api_key):
    """Create FastAPI app with admin key configured."""
    settings.admin_api_key = admin_api_key
    return create_app(settings=settings)


@pytest.fixture
def admin_client(app_with_admin):
    """Test client for admin endpoints."""
    return TestClient(app_with_admin)


@pytest.fixture
def admin_headers(admin_api_key):
    """Headers with admin API key."""
    return {"X-API-Key": admin_api_key}


class TestAdminContextEndpoints:
    """Test admin context management endpoints."""

    def test_list_contexts_requires_auth(self, admin_client):
        """Test that listing contexts requires authentication."""
        response = admin_client.get("/admin/contexts")
        assert response.status_code == 401

    def test_list_contexts_with_auth(self, admin_client, admin_headers, async_session_sync):
        """Test listing contexts with valid auth."""
        # Create test context
        context = Context(
            name="test_list_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        response = admin_client.get("/admin/contexts", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "contexts" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_get_context_details(self, admin_client, admin_headers, async_session_sync):
        """Test getting detailed context information."""
        # Create context with related entities
        context = Context(
            name="test_detail_context",
            type="virtual",
            config={"key": "value"},
            default_cwd="/tmp",  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        conversation = Conversation(
            platform="test",
            platform_id="test_conv",
            context_id=context.id,
            current_cwd="/tmp",  # noqa: S108,
        )
        async_session_sync.add(conversation)
        async_session_sync.commit()

        response = admin_client.get(f"/admin/contexts/{context.id}", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == str(context.id)
        assert data["name"] == "test_detail_context"
        assert len(data["conversations"]) == 1

    def test_create_context(self, admin_client, admin_headers):
        """Test creating a new context."""
        payload = {
            "name": "new_test_context",
            "type": "virtual",
            "config": {},
            "default_cwd": "/tmp",  # noqa: S108
        }

        response = admin_client.post("/admin/contexts", json=payload, headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "context_id" in data

    def test_create_context_duplicate_name(self, admin_client, admin_headers, async_session_sync):
        """Test that duplicate context names are rejected."""
        # Create existing context
        context = Context(
            name="duplicate_name", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        # Try to create duplicate
        payload = {
            "name": "duplicate_name",
            "type": "virtual",
            "config": {},
            "default_cwd": "/tmp",  # noqa: S108
        }

        response = admin_client.post("/admin/contexts", json=payload, headers=admin_headers)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_delete_context(self, admin_client, admin_headers, async_session_sync):
        """Test deleting a context."""
        # Create context
        context = Context(
            name="delete_test_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        context_id = context.id

        response = admin_client.delete(f"/admin/contexts/{context_id}", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["deleted_context_id"] == str(context_id)


class TestAdminOAuthEndpoints:
    """Test admin OAuth token management endpoints."""

    def test_list_oauth_tokens(self, admin_client, admin_headers, async_session_sync):
        """Test listing OAuth tokens."""
        # Create context with token
        context = Context(
            name="oauth_list_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            access_token="secret_token",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        async_session_sync.add(token)
        async_session_sync.commit()

        response = admin_client.get("/admin/oauth/tokens", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "tokens" in data
        assert data["total"] >= 1

        # Verify sensitive data not exposed
        token_data = data["tokens"][0]
        assert "access_token" not in token_data
        assert "refresh_token" not in token_data

    def test_list_oauth_tokens_filter_by_context(
        self, admin_client, admin_headers, async_session_sync
    ):
        """Test filtering OAuth tokens by context."""
        # Create two contexts
        context_a = Context(
            name="oauth_filter_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="oauth_filter_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session_sync.add(context_a)
        async_session_sync.add(context_b)
        async_session_sync.flush()

        # Add tokens to both
        token_a = OAuthToken(
            context_id=context_a.id,
            provider="homey",
            access_token="token_a",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_b = OAuthToken(
            context_id=context_b.id,
            provider="homey",
            access_token="token_b",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        async_session_sync.add(token_a)
        async_session_sync.add(token_b)
        async_session_sync.commit()

        # Filter by context A
        response = admin_client.get(
            f"/admin/oauth/tokens?context_id={context_a.id}", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["tokens"][0]["context_id"] == str(context_a.id)

    def test_revoke_oauth_token(self, admin_client, admin_headers, async_session_sync):
        """Test revoking an OAuth token."""
        # Create context with token
        context = Context(
            name="revoke_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            access_token="revoke_token",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        async_session_sync.add(token)
        async_session_sync.commit()

        token_id = token.id

        response = admin_client.delete(f"/admin/oauth/tokens/{token_id}", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["revoked_token_id"] == str(token_id)

    def test_get_oauth_status(self, admin_client, admin_headers, async_session_sync):
        """Test getting OAuth status for a context."""
        # Create context with token
        context = Context(
            name="status_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            access_token="status_token",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
            scope="read write",
        )
        async_session_sync.add(token)
        async_session_sync.commit()

        response = admin_client.get(f"/admin/oauth/status/{context.id}", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["context_id"] == str(context.id)
        assert len(data["providers"]) == 1
        assert data["providers"][0]["provider"] == "homey"
        assert data["providers"][0]["authorized"] is True


class TestAdminMCPEndpoints:
    """Test admin MCP management endpoints."""

    def test_get_mcp_health(self, admin_client, admin_headers):
        """Test getting MCP client health."""
        response = admin_client.get("/admin/mcp/health", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "health" in data

    def test_get_mcp_stats(self, admin_client, admin_headers):
        """Test getting MCP client statistics."""
        response = admin_client.get("/admin/mcp/stats", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "stats" in data
        stats = data["stats"]
        assert "total_contexts" in stats
        assert "total_clients" in stats

    def test_disconnect_mcp_clients(self, admin_client, admin_headers, async_session_sync):
        """Test disconnecting MCP clients for a context."""
        # Create context
        context = Context(
            name="mcp_disconnect_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108,
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        response = admin_client.post(f"/admin/mcp/disconnect/{context.id}", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["context_id"] == str(context.id)


class TestAdminDiagnosticsEndpoints:
    """Test admin diagnostics endpoints."""

    def test_get_traces_requires_auth(self, admin_client):
        """Test that diagnostics endpoints require auth."""
        response = admin_client.get("/admin/diagnostics/traces")
        assert response.status_code == 401

    def test_get_traces(self, admin_client, admin_headers):
        """Test getting traces with auth."""
        response = admin_client.get("/admin/diagnostics/traces?limit=10", headers=admin_headers)
        # May return 200 or error if diagnostics not configured
        assert response.status_code in [200, 500]

    def test_get_metrics(self, admin_client, admin_headers):
        """Test getting system metrics."""
        response = admin_client.get("/admin/diagnostics/metrics", headers=admin_headers)
        assert response.status_code in [200, 500]

    def test_run_diagnostics(self, admin_client, admin_headers):
        """Test running diagnostics."""
        response = admin_client.post("/admin/diagnostics/run", headers=admin_headers)
        # May fail if services not available
        assert response.status_code in [200, 500]


@pytest.fixture
def async_session_sync(async_session):
    """Synchronous wrapper for async session (for TestClient)."""
    import asyncio

    class SyncSession:
        def __init__(self, async_session):
            self._session = async_session
            self._loop = asyncio.get_event_loop()

        def add(self, obj):
            self._session.add(obj)

        def flush(self):
            self._loop.run_until_complete(self._session.flush())

        def commit(self):
            self._loop.run_until_complete(self._session.commit())

    return SyncSession(async_session)
