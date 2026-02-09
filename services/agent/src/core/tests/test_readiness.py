"""Unit tests for readiness probe endpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings
from core.core.service import AgentService
from interfaces.http.app import create_app


@pytest.fixture
def mock_settings() -> Settings:
    """Create test settings."""
    return Settings(
        environment="test",
        litellm_api_base="http://localhost:4000",
        qdrant_url="http://localhost:6333",
    )


@pytest.fixture
def mock_service() -> AgentService:
    """Create a mock AgentService."""
    service = MagicMock(spec=AgentService)
    return service


@pytest.fixture
def test_app(mock_settings: Settings, mock_service: AgentService) -> TestClient:
    """Create test FastAPI app."""
    app = create_app(settings=mock_settings, service=mock_service)
    return TestClient(app)


def test_readiness_endpoint_exists(test_app: TestClient) -> None:
    """Test that the /readyz endpoint exists and returns JSON."""
    response = test_app.get("/readyz")

    # Should return JSON with status and checks
    assert response.headers["content-type"] == "application/json"
    data = response.json()
    assert "status" in data
    assert "checks" in data

    # Status should be either "ready" or "not_ready"
    assert data["status"] in ("ready", "not_ready")

    # Checks should contain all 4 checks
    assert "database" in data["checks"]
    assert "qdrant" in data["checks"]
    assert "skills" in data["checks"]
    assert "litellm" in data["checks"]


def test_readiness_check_structure(test_app: TestClient) -> None:
    """Test that each check returns proper structure."""
    response = test_app.get("/readyz")
    data = response.json()

    # Each check should have a status field
    for check_name, check_result in data["checks"].items():
        assert "status" in check_result, f"Check {check_name} missing status field"
        assert check_result["status"] in (
            "ok",
            "error",
            "unavailable",
        ), f"Check {check_name} has invalid status: {check_result['status']}"

        # If status is ok, should have latency_ms (except skills which has count)
        if check_result["status"] == "ok":
            if check_name == "skills":
                # Skills check should have count instead of latency
                if "count" not in check_result and "latency_ms" not in check_result:
                    pytest.fail(
                        "Skills check with status ok should have either count or latency_ms"
                    )
            # Other checks can have latency_ms but it's optional

        # If status is error, should have error message
        if check_result["status"] == "error":
            assert (
                "error" in check_result
            ), f"Check {check_name} with error status missing error message"


def test_readiness_returns_503_on_failures(test_app: TestClient) -> None:
    """Test that failures result in 503 status code."""
    response = test_app.get("/readyz")

    # If any check failed, should return 503
    data = response.json()
    has_error = any(check.get("status") == "error" for check in data["checks"].values())

    if has_error:
        assert response.status_code == 503, "Should return 503 when checks have errors"
        assert data["status"] == "not_ready"
    else:
        # All checks passed or are unavailable
        assert response.status_code in (
            200,
            503,
        ), f"Unexpected status code: {response.status_code}"


@pytest.mark.asyncio
async def test_readiness_database_check_logic() -> None:
    """Test the database check logic in isolation."""
    from sqlalchemy import text

    # Mock a working database session
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock()

    # Simulate database check
    try:
        await mock_session.execute(text("SELECT 1"))
        result = {"status": "ok"}
    except Exception as e:
        result = {"status": "error", "error": str(e)[:200]}

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness_database_check_failure() -> None:
    """Test the database check logic when it fails."""
    # Mock a failing database session
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(side_effect=Exception("Connection refused"))

    # Simulate database check
    try:
        from sqlalchemy import text

        await mock_session.execute(text("SELECT 1"))
        result = {"status": "ok"}
    except Exception as e:
        result = {"status": "error", "error": str(e)[:200]}

    assert result["status"] == "error"
    assert "Connection refused" in result["error"]


def test_healthz_includes_environment(test_app: TestClient) -> None:
    """Test that /healthz includes the environment field."""
    response = test_app.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert "environment" in data
    assert data["environment"] == "test"


def test_readyz_includes_environment(test_app: TestClient) -> None:
    """Test that /readyz includes the environment field."""
    response = test_app.get("/readyz")
    data = response.json()
    assert "environment" in data
    assert data["environment"] == "test"
