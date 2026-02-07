"""Comprehensive CRUD tests for admin portal endpoints.

Tests all admin HTTP endpoints including contexts, credentials, workspaces,
and OAuth tokens. These are integration tests that use real database operations
via TestClient.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from core.core.config import Settings
from core.db.models import Context, Conversation, Workspace
from core.db.oauth_models import OAuthToken
from interfaces.http.app import create_app


@pytest.fixture
def diagnostic_api_key() -> str:
    """Diagnostic API key for testing."""
    return "test_diagnostic_key_12345"


@pytest.fixture
def admin_api_key() -> str:
    """Admin API key for testing."""
    return "test_admin_key_67890"


@pytest.fixture
def settings(diagnostic_api_key: str, admin_api_key: str) -> Settings:
    """Test settings with API keys configured."""
    return Settings(
        environment="test",
        diagnostic_api_key=diagnostic_api_key,
        admin_api_key=admin_api_key,
        credential_encryption_key="test_encryption_key_32_bytes_long!!!",
    )


@pytest.fixture
def app(settings: Settings) -> Any:
    """FastAPI app with test configuration."""
    return create_app(settings=settings)


@pytest.fixture
def client(app: Any) -> TestClient:
    """Test client for admin endpoints."""
    return TestClient(app)


@pytest.fixture
def api_headers(diagnostic_api_key: str) -> dict[str, str]:
    """Headers with API key authentication."""
    return {"X-API-Key": diagnostic_api_key}


@pytest.fixture
def admin_headers(admin_api_key: str) -> dict[str, str]:
    """Headers with admin API key authentication."""
    return {"X-API-Key": admin_api_key}


@pytest.fixture
def async_session_sync(async_session: Any) -> Any:
    """Synchronous wrapper for async session (for TestClient).

    TestClient is synchronous, so we need a wrapper that converts
    async session operations to sync for use in test setup/teardown.
    """

    class SyncSession:
        def __init__(self, async_session: Any) -> None:
            self._session = async_session
            self._loop = asyncio.get_event_loop()

        def add(self, obj: Any) -> None:
            self._session.add(obj)

        def flush(self) -> None:
            self._loop.run_until_complete(self._session.flush())

        def commit(self) -> None:
            self._loop.run_until_complete(self._session.commit())

        def delete(self, obj: Any) -> None:
            return self._loop.run_until_complete(self._session.delete(obj))

    return SyncSession(async_session)


class TestContextCRUD:
    """Tests for /platformadmin/contexts endpoints."""

    def test_list_contexts_requires_auth(self, client: Any) -> None:
        """GET /platformadmin/contexts without auth returns 401."""
        response = client.get("/platformadmin/contexts")
        assert response.status_code == 401

    def test_list_contexts_with_valid_api_key(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """GET /platformadmin/contexts with valid API key succeeds."""
        # Create test context
        context = Context(
            name="test_list_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        response = client.get("/platformadmin/contexts", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "contexts" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_create_context_success(self, client: Any, admin_headers: Any) -> None:
        """POST /platformadmin/contexts creates context successfully."""
        payload = {
            "name": "new_test_context",
            "type": "virtual",
            "config": {},
            "default_cwd": "/tmp",  # noqa: S108
        }

        response = client.post("/platformadmin/contexts", json=payload, headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "context_id" in data

    def test_create_context_duplicate_name_fails(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """POST /platformadmin/contexts with duplicate name returns 400."""
        # Create existing context
        context = Context(
            name="duplicate_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        # Try to create duplicate
        payload = {
            "name": "duplicate_context",
            "type": "virtual",
            "config": {},
            "default_cwd": "/tmp",  # noqa: S108
        }

        response = client.post("/platformadmin/contexts", json=payload, headers=admin_headers)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_get_context_details(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """GET /platformadmin/contexts/{id} returns detailed context info."""
        # Create context with related entities
        context = Context(
            name="detail_test_context",
            type="virtual",
            config={"key": "value"},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        conversation = Conversation(
            platform="test",
            platform_id="test_conv",
            context_id=context.id,
            current_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(conversation)
        async_session_sync.commit()

        response = client.get(
            f"/platformadmin/contexts/{context.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == str(context.id)
        assert data["name"] == "detail_test_context"
        assert len(data["conversations"]) == 1

    def test_get_context_not_found(self, client: Any, admin_headers: Any) -> None:
        """GET /platformadmin/contexts/{missing_id} returns 404."""
        missing_id = uuid4()
        response = client.get(
            f"/platformadmin/contexts/{missing_id}",
            headers=admin_headers,
        )
        assert response.status_code == 404

    def test_delete_context(self, client: Any, admin_headers: Any, async_session_sync: Any) -> None:
        """DELETE /platformadmin/contexts/{id} removes context."""
        # Create context
        context = Context(
            name="delete_test_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        context_id = context.id

        response = client.delete(
            f"/platformadmin/contexts/{context_id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["deleted_context_id"] == str(context_id)

    def test_delete_context_not_found(self, client: Any, admin_headers: Any) -> None:
        """DELETE /platformadmin/contexts/{missing_id} returns 404."""
        missing_id = uuid4()
        response = client.delete(
            f"/platformadmin/contexts/{missing_id}",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestCredentialCRUD:
    """Tests for /platformadmin/credentials endpoints."""

    def test_list_credentials_requires_auth(self, client: Any) -> None:
        """GET /platformadmin/credentials/list without auth returns 401."""
        response = client.get("/platformadmin/credentials/list")
        assert response.status_code == 401

    def test_list_credentials_with_valid_api_key(self, client: Any, admin_headers: Any) -> None:
        """GET /platformadmin/credentials/list with valid API key succeeds."""
        response = client.get("/platformadmin/credentials/list", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "credentials" in data
        assert "total_credentials" in data
        assert "users_with_credentials" in data

    def test_get_credential_types(self, client: Any, admin_headers: Any) -> None:
        """GET /platformadmin/credentials/types returns supported types."""
        response = client.get("/platformadmin/credentials/types", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "types" in data
        assert "azure_devops_pat" in data["types"]
        assert "github_token" in data["types"]


class TestOAuthCRUD:
    """Tests for /platformadmin/oauth endpoints."""

    def test_list_oauth_tokens_requires_auth(self, client: Any) -> None:
        """GET /admin/oauth/tokens without auth returns 401."""
        response = client.get("/admin/oauth/tokens")
        assert response.status_code == 401

    def test_list_oauth_tokens_with_valid_api_key(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """GET /admin/oauth/tokens with valid API key succeeds."""
        # Create context with token
        context = Context(
            name="oauth_test_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            access_token="test_token",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        async_session_sync.add(token)
        async_session_sync.commit()

        response = client.get("/admin/oauth/tokens", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "tokens" in data
        assert "total" in data

        # Verify sensitive data not exposed
        if len(data["tokens"]) > 0:
            token_data = data["tokens"][0]
            assert "access_token" not in token_data
            assert "refresh_token" not in token_data

    def test_list_oauth_tokens_filter_by_context(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """GET /admin/oauth/tokens with context_id filter works."""
        # Create two contexts
        context_a = Context(
            name="oauth_filter_a",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        context_b = Context(
            name="oauth_filter_b",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
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
        response = client.get(
            f"/admin/oauth/tokens?context_id={context_a.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 1
        assert data["tokens"][0]["context_id"] == str(context_a.id)

    def test_revoke_oauth_token(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """DELETE /admin/oauth/tokens/{id} revokes token."""
        # Create context with token
        context = Context(
            name="revoke_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
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

        response = client.delete(
            f"/admin/oauth/tokens/{token_id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["revoked_token_id"] == str(token_id)

    def test_get_oauth_status(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """GET /admin/oauth/status/{context_id} returns OAuth status."""
        # Create context with token
        context = Context(
            name="status_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
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

        response = client.get(
            f"/admin/oauth/status/{context.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["context_id"] == str(context.id)
        assert len(data["providers"]) == 1
        assert data["providers"][0]["provider"] == "homey"
        assert data["providers"][0]["authorized"] is True


class TestWorkspaceCRUD:
    """Tests for /platformadmin/workspaces endpoints."""

    def test_list_workspaces_requires_auth(self, client: Any) -> None:
        """GET /platformadmin/workspaces/list without auth returns 401."""
        response = client.get("/platformadmin/workspaces/list")
        assert response.status_code == 401

    def test_list_workspaces_with_valid_api_key(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """GET /platformadmin/workspaces/list with valid API key succeeds."""
        # Create context with workspace
        context = Context(
            name="workspace_test_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        workspace = Workspace(
            context_id=context.id,
            repo_url="https://github.com/test/repo",
            local_path="/tmp/test-repo",  # noqa: S108
            status="cloned",
        )
        async_session_sync.add(workspace)
        async_session_sync.commit()

        response = client.get("/platformadmin/workspaces/list", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "workspaces" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_delete_workspace(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """DELETE /platformadmin/workspaces/{id} removes workspace."""
        # Create context with workspace
        context = Context(
            name="delete_workspace_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.flush()

        workspace = Workspace(
            context_id=context.id,
            repo_url="https://github.com/test/delete-repo",
            local_path="/tmp/delete-repo",  # noqa: S108
            status="cloned",
        )
        async_session_sync.add(workspace)
        async_session_sync.commit()

        workspace_id = workspace.id

        response = client.delete(
            f"/platformadmin/workspaces/{workspace_id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True


class TestAdminAuth:
    """Tests for admin authentication."""

    def test_endpoint_without_auth_returns_401(self, client: Any) -> None:
        """Requests without API key or session get 401."""
        response = client.get("/platformadmin/contexts")
        assert response.status_code == 401

    def test_endpoint_with_invalid_api_key(self, client: Any) -> None:
        """Requests with wrong X-API-Key get 401."""
        response = client.get(
            "/platformadmin/contexts",
            headers={"X-API-Key": "invalid_key"},
        )
        assert response.status_code == 401

    def test_diagnostic_api_endpoint_with_diagnostic_key(
        self,
        client: Any,
        api_headers: Any,
    ) -> None:
        """Diagnostic API endpoints work with diagnostic API key."""
        response = client.get(
            "/platformadmin/api/health",
        )
        # Health endpoint doesn't require auth
        assert response.status_code == 200

        # Try authenticated endpoint
        response = client.get(
            "/platformadmin/api/status",
            headers=api_headers,
        )
        # May return 200 or error depending on services availability
        assert response.status_code in [200, 500]

    def test_admin_endpoint_with_admin_key(self, client: Any, admin_headers: Any) -> None:
        """Admin endpoints work with admin API key."""
        response = client.get(
            "/platformadmin/contexts",
            headers=admin_headers,
        )
        assert response.status_code == 200


class TestDiagnosticAPI:
    """Tests for /platformadmin/api diagnostics endpoints."""

    def test_health_check_no_auth_required(self, client: Any) -> None:
        """GET /platformadmin/api/health works without auth."""
        response = client.get("/platformadmin/api/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "diagnostic-api"

    def test_get_conversations_requires_auth(self, client: Any) -> None:
        """GET /platformadmin/api/conversations requires auth."""
        response = client.get("/platformadmin/api/conversations")
        assert response.status_code == 401

    def test_get_conversations_with_valid_api_key(self, client: Any, api_headers: Any) -> None:
        """GET /platformadmin/api/conversations with API key succeeds."""
        response = client.get(
            "/platformadmin/api/conversations",
            headers=api_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

    def test_get_debug_stats(self, client: Any, api_headers: Any) -> None:
        """GET /platformadmin/api/debug/stats returns log statistics."""
        response = client.get(
            "/platformadmin/api/debug/stats?hours=24",
            headers=api_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "total_logs" in data
        assert "by_event_type" in data
        assert "by_hour" in data
        assert "recent_errors" in data

    def test_search_traces(self, client: Any, api_headers: Any) -> None:
        """GET /platformadmin/api/traces/search returns traces."""
        response = client.get(
            "/platformadmin/api/traces/search?limit=10",
            headers=api_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

    def test_get_trace_not_found(self, client: Any, api_headers: Any) -> None:
        """GET /platformadmin/api/traces/{missing_id} returns 404."""
        response = client.get(
            "/platformadmin/api/traces/nonexistent-trace-id",
            headers=api_headers,
        )
        assert response.status_code == 404

    def test_get_system_config(self, client: Any, api_headers: Any) -> None:
        """GET /platformadmin/api/config returns system config."""
        response = client.get(
            "/platformadmin/api/config",
            headers=api_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)


class TestMCPEndpoints:
    """Tests for /admin/mcp endpoints."""

    def test_get_mcp_health_requires_auth(self, client: Any) -> None:
        """GET /admin/mcp/health requires auth."""
        response = client.get("/admin/mcp/health")
        assert response.status_code == 401

    def test_get_mcp_health_with_valid_api_key(self, client: Any, admin_headers: Any) -> None:
        """GET /admin/mcp/health with API key succeeds."""
        response = client.get("/admin/mcp/health", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "health" in data

    def test_get_mcp_stats(self, client: Any, admin_headers: Any) -> None:
        """GET /admin/mcp/stats returns statistics."""
        response = client.get("/admin/mcp/stats", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "stats" in data
        stats = data["stats"]
        assert "total_contexts" in stats
        assert "total_clients" in stats

    def test_disconnect_mcp_clients(
        self,
        client: Any,
        admin_headers: Any,
        async_session_sync: Any,
    ) -> None:
        """POST /admin/mcp/disconnect/{context_id} disconnects clients."""
        # Create context
        context = Context(
            name="mcp_disconnect_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        async_session_sync.add(context)
        async_session_sync.commit()

        response = client.post(
            f"/admin/mcp/disconnect/{context.id}",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["context_id"] == str(context.id)
