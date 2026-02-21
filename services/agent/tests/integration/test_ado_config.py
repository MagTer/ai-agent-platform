"""Integration tests for ADO team configuration CRUD endpoints.

These tests use TestClient with an in-memory SQLite database and
mocked admin authentication. They do NOT require a running stack.

Run manually:
    pytest tests/integration/test_ado_config.py -v
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.db.models import Base
from core.runtime.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(
        environment="test",
        litellm_api_base="http://localhost:4000",
        qdrant_url="http://localhost:6333",
    )


@pytest.fixture(scope="module")
def test_app(settings: Settings) -> FastAPI:
    """Create a FastAPI app with in-memory SQLite and mocked admin auth."""
    from core.auth.header_auth import UserIdentity
    from interfaces.http.admin_auth import AdminUser, verify_admin_user
    from interfaces.http.app import create_app

    app = create_app(settings=settings)

    # Build a fake admin user (no DB lookup needed)
    mock_db_user = MagicMock()
    mock_db_user.id = "00000000-0000-0000-0000-000000000001"
    mock_db_user.email = "admin@test.example"
    mock_db_user.display_name = "Test Admin"
    mock_db_user.role = "admin"
    mock_db_user.is_active = True

    fake_admin = AdminUser(
        identity=UserIdentity(
            email="admin@test.example",
            name="Test Admin",
            openwebui_id=None,
            role="admin",
        ),
        db_user=mock_db_user,
    )

    # Override admin auth to always return the fake admin
    app.dependency_overrides[verify_admin_user] = lambda: fake_admin

    return app


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all tables created fresh for each test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def client(test_app: FastAPI, db_session: AsyncSession) -> TestClient:
    """TestClient with the in-memory DB injected."""
    from core.db.engine import get_db

    async def _get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    test_app.dependency_overrides[get_db] = _get_db
    return TestClient(test_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAdoTeamsGet:
    """GET /platformadmin/ado-config/teams"""

    def test_list_teams_empty_on_fresh_db(self, client: TestClient) -> None:
        """Returns 200 with an empty list when no teams are configured."""
        response = client.get("/platformadmin/ado-config/teams")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    def test_list_teams_unauthenticated_returns_401(
        self, test_app: FastAPI, db_session: AsyncSession
    ) -> None:
        """Without auth override, endpoint returns 401."""
        from interfaces.http.admin_auth import verify_admin_user

        # Verify that removing the override causes 401
        saved = test_app.dependency_overrides.pop(verify_admin_user, None)
        try:
            no_auth_client = TestClient(test_app, raise_server_exceptions=False)
            response = no_auth_client.get("/platformadmin/ado-config/teams")
            assert response.status_code in (401, 403)
        finally:
            if saved is not None:
                test_app.dependency_overrides[verify_admin_user] = saved


class TestAdoDefaultsGet:
    """GET /platformadmin/ado-config/defaults"""

    def test_get_defaults_returns_empty_when_not_configured(self, client: TestClient) -> None:
        """Returns 200 with empty area_path when no defaults row exists."""
        response = client.get("/platformadmin/ado-config/defaults")
        assert response.status_code == 200
        data = response.json()
        assert "area_path" in data
        assert "default_type" in data
        assert data["area_path"] == ""


class TestAdoTeamsPost:
    """POST /platformadmin/ado-config/teams"""

    def test_create_team_returns_201_shape(self, client: TestClient) -> None:
        """Creating a team returns the created resource with expected fields."""
        payload: dict[str, Any] = {
            "alias": "infra",
            "display_name": "Infrastructure",
            "area_path": "Web Teams\\Infra",
            "owner": "Jane Smith",
            "default_type": "User Story",
            "default_tags": ["Security"],
            "sort_order": 1,
        }
        response = client.post("/platformadmin/ado-config/teams", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["alias"] == "infra"
        assert data["area_path"] == "Web Teams\\Infra"
        assert data["default_type"] == "User Story"
        assert data["default_tags"] == ["Security"]
        assert "id" in data

    def test_create_team_alias_lowercased(self, client: TestClient) -> None:
        """Alias is normalised to lowercase on creation."""
        payload: dict[str, Any] = {
            "alias": "PLATFORM",
            "area_path": "Web Teams\\Platform",
            "default_type": "Feature",
        }
        response = client.post("/platformadmin/ado-config/teams", json=payload)
        assert response.status_code == 200
        assert response.json()["alias"] == "platform"

    def test_create_team_duplicate_alias_returns_409(self, client: TestClient) -> None:
        """Duplicate alias returns 409 Conflict."""
        payload: dict[str, Any] = {
            "alias": "duplicate-team",
            "area_path": "Web Teams\\Dup",
            "default_type": "Bug",
        }
        # First creation succeeds
        r1 = client.post("/platformadmin/ado-config/teams", json=payload)
        assert r1.status_code == 200

        # Second creation fails
        r2 = client.post("/platformadmin/ado-config/teams", json=payload)
        assert r2.status_code == 409


class TestAdoTeamsPut:
    """PUT /platformadmin/ado-config/teams/{alias}"""

    def test_update_team_returns_updated_data(self, client: TestClient) -> None:
        """Updating an existing team returns the modified resource."""
        # Create team first
        create_payload: dict[str, Any] = {
            "alias": "to-update",
            "area_path": "Web Teams\\Old",
            "default_type": "User Story",
        }
        client.post("/platformadmin/ado-config/teams", json=create_payload)

        # Update it
        update_payload: dict[str, Any] = {
            "alias": "to-update",
            "area_path": "Web Teams\\New",
            "default_type": "Feature",
            "default_tags": ["Updated"],
            "sort_order": 5,
        }
        response = client.put("/platformadmin/ado-config/teams/to-update", json=update_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["area_path"] == "Web Teams\\New"
        assert data["default_type"] == "Feature"
        assert data["default_tags"] == ["Updated"]
        assert data["sort_order"] == 5

    def test_update_unknown_alias_returns_404(self, client: TestClient) -> None:
        """Updating a nonexistent alias returns 404."""
        payload: dict[str, Any] = {
            "alias": "ghost",
            "area_path": "Web Teams\\Ghost",
            "default_type": "Bug",
        }
        response = client.put("/platformadmin/ado-config/teams/ghost", json=payload)
        assert response.status_code == 404


class TestAdoTeamsDelete:
    """DELETE /platformadmin/ado-config/teams/{alias}"""

    def test_delete_team_returns_204(self, client: TestClient) -> None:
        """Deleting an existing team returns 204 No Content."""
        # Create team first
        create_payload: dict[str, Any] = {
            "alias": "to-delete",
            "area_path": "Web Teams\\ToDelete",
            "default_type": "Bug",
        }
        client.post("/platformadmin/ado-config/teams", json=create_payload)

        # Delete it
        response = client.delete("/platformadmin/ado-config/teams/to-delete")
        assert response.status_code == 204

        # Confirm it's gone
        list_response = client.get("/platformadmin/ado-config/teams")
        aliases = [t["alias"] for t in list_response.json()]
        assert "to-delete" not in aliases

    def test_delete_unknown_alias_returns_404(self, client: TestClient) -> None:
        """Deleting a nonexistent alias returns 404."""
        response = client.delete("/platformadmin/ado-config/teams/nonexistent")
        assert response.status_code == 404


class TestAdoDefaultsPut:
    """PUT /platformadmin/ado-config/defaults"""

    def test_update_defaults_creates_on_first_call(self, client: TestClient) -> None:
        """PUT defaults creates the defaults row if it doesn't exist."""
        payload = {"area_path": "Web Teams\\All", "default_type": "Feature"}
        response = client.put("/platformadmin/ado-config/defaults", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["area_path"] == "Web Teams\\All"
        assert data["default_type"] == "Feature"

    def test_update_defaults_overwrites_existing(self, client: TestClient) -> None:
        """Calling PUT defaults twice updates the same row."""
        client.put(
            "/platformadmin/ado-config/defaults",
            json={"area_path": "Web Teams\\First", "default_type": "Feature"},
        )
        client.put(
            "/platformadmin/ado-config/defaults",
            json={"area_path": "Web Teams\\Second", "default_type": "Bug"},
        )
        get_response = client.get("/platformadmin/ado-config/defaults")
        data = get_response.json()
        assert data["area_path"] == "Web Teams\\Second"
        assert data["default_type"] == "Bug"

    def test_update_defaults_invalid_type_returns_422(self, client: TestClient) -> None:
        """Invalid default_type returns 422 Unprocessable Entity."""
        payload = {"area_path": "Web Teams\\Bad", "default_type": "InvalidType"}
        response = client.put("/platformadmin/ado-config/defaults", json=payload)
        assert response.status_code == 422
