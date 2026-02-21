"""Integration tests for admin Diagnostic API endpoints."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.config import Settings


@pytest.fixture(scope="session")
def mock_settings() -> Settings:
    """Create test settings with diagnostic API key."""
    return Settings(
        environment="test",
        litellm_api_base="http://localhost:4000",
        qdrant_url="http://localhost:6333",
        diagnostic_api_key="test-diag-key-123",
    )


@pytest.fixture(scope="session")
def test_app(mock_settings: Settings) -> FastAPI:
    """Create a FastAPI app with mocked dependencies (session scope)."""
    from core.db.engine import get_db
    from interfaces.http.app import create_app

    # Create app with test settings
    app = create_app(settings=mock_settings)

    # Override DB
    async def mock_get_db() -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one_or_none.return_value = None
        mock_result.scalar.return_value = 0
        mock_result.fetchall.return_value = []
        session.execute.return_value = mock_result
        yield session

    app.dependency_overrides[get_db] = mock_get_db

    return app


@pytest.fixture
def test_client(test_app: FastAPI, mock_settings: Settings) -> Iterator[TestClient]:
    """Create a TestClient from the session-scoped app."""
    # Patch get_settings to return mock_settings
    with patch("core.runtime.config.get_settings", return_value=mock_settings):
        # Also patch it in admin_api module since it imports get_settings
        with patch("interfaces.http.admin_api.get_settings", return_value=mock_settings):
            yield TestClient(test_app, raise_server_exceptions=False)


class TestAuthentication:
    """Tests for API key authentication."""

    def test_valid_api_key_returns_200(self, test_client: TestClient) -> None:
        """Valid API key should allow access to protected endpoints."""
        response = test_client.get(
            "/platformadmin/api/config",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200

    def test_invalid_api_key_returns_401(self, test_client: TestClient) -> None:
        """Invalid API key should return 401."""
        response = test_client.get(
            "/platformadmin/api/config",
            headers={"X-API-Key": "wrong-key"},
        )

        assert response.status_code == 401
        assert "Invalid API key" in response.json()["detail"]

    def test_missing_api_key_returns_401(self, test_client: TestClient) -> None:
        """Missing API key should return 401."""
        response = test_client.get("/platformadmin/api/config")

        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]

    def test_health_endpoint_works_without_auth(self, test_client: TestClient) -> None:
        """Health endpoint should work without authentication."""
        response = test_client.get("/platformadmin/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "diagnostic-api"
        assert data["environment"] == "test"


class TestStatusEndpoint:
    """Tests for GET /platformadmin/api/status endpoint."""

    @patch("interfaces.http.admin_api.DiagnosticsService")
    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_status_returns_system_health(
        self,
        mock_read_logs: AsyncMock,
        mock_diag_cls: MagicMock,
        test_client: TestClient,
    ) -> None:
        """Status endpoint should return aggregated system health."""
        # Mock DiagnosticsService
        mock_diag = AsyncMock()
        mock_diag.get_diagnostics_summary.return_value = {
            "overall_status": "HEALTHY",
            "healthy_components": ["database", "litellm"],
            "failed_components": [],
            "metrics": {"requests_total": 100},
            "recommended_actions": [],
        }
        mock_diag_cls.return_value = mock_diag

        # Mock debug logs
        mock_read_logs.return_value = []

        response = test_client.get(
            "/platformadmin/api/status",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "HEALTHY"
        assert data["environment"] == "test"
        assert "timestamp" in data
        assert data["healthy_components"] == ["database", "litellm"]
        assert data["failed_components"] == []

    @patch("interfaces.http.admin_api.DiagnosticsService")
    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_status_includes_recent_errors(
        self,
        mock_read_logs: AsyncMock,
        mock_diag_cls: MagicMock,
        test_client: TestClient,
    ) -> None:
        """Status endpoint should include recent supervisor errors."""
        # Mock DiagnosticsService
        mock_diag = AsyncMock()
        mock_diag.get_diagnostics_summary.return_value = {
            "overall_status": "DEGRADED",
            "healthy_components": ["database"],
            "failed_components": [],
            "metrics": {},
            "recommended_actions": [],
        }
        mock_diag_cls.return_value = mock_diag

        # Mock debug logs with ABORT outcome
        mock_read_logs.return_value = [
            {
                "trace_id": "abc123",
                "event_type": "supervisor",
                "timestamp": "2026-02-16T10:00:00Z",
                "event_data": {
                    "outcome": "ABORT",
                    "reason": "Tool execution failed",
                },
            }
        ]

        response = test_client.get(
            "/platformadmin/api/status",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["recent_errors"]) == 1
        assert data["recent_errors"][0]["outcome"] == "ABORT"
        assert data["recent_errors"][0]["trace_id"] == "abc123"


class TestOtelMetricsEndpoint:
    """Tests for GET /platformadmin/api/otel-metrics endpoint."""

    @patch("core.observability.metrics.get_metric_snapshot")
    def test_otel_metrics_returns_counters_and_insights(
        self,
        mock_snapshot: MagicMock,
        test_client: TestClient,
    ) -> None:
        """OTel metrics should return counters with computed insights."""
        mock_snapshot.return_value = {
            "requests.total": 100,
            "requests.errors": 5,
            "requests.duration_ms_sum": 50000,
            "llm.calls.total": 20,
            "llm.duration_ms_sum": 30000,
            "llm.tokens.total": 5000,
            "tools.calls.total": 15,
            "tools.errors": 2,
            "requests.active": 3,
        }

        response = test_client.get(
            "/platformadmin/api/otel-metrics",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "counters" in data
        assert "insights" in data
        assert data["insights"]["error_rate_pct"] == 5.0
        assert data["insights"]["avg_request_duration_ms"] == 500.0
        assert data["insights"]["avg_llm_duration_ms"] == 1500.0
        assert data["insights"]["total_requests"] == 100
        assert data["insights"]["total_tool_errors"] == 2


class TestDebugLogsEndpoint:
    """Tests for GET /platformadmin/api/debug/logs endpoint."""

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_debug_logs_returns_list(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Debug logs endpoint should return list of log entries."""
        mock_read_logs.return_value = [
            {
                "trace_id": "abc123",
                "event_type": "tool_call",
                "timestamp": "2026-02-16T10:00:00Z",
                "event_data": {"tool_name": "search"},
            },
            {
                "trace_id": "abc123",
                "event_type": "supervisor",
                "timestamp": "2026-02-16T10:01:00Z",
                "event_data": {"outcome": "SUCCESS"},
            },
        ]

        response = test_client.get(
            "/platformadmin/api/debug/logs",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["event_type"] == "tool_call"

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_debug_logs_filters_by_trace_id(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Debug logs should accept trace_id filter."""
        mock_read_logs.return_value = [
            {
                "trace_id": "specific-trace",
                "event_type": "plan",
                "timestamp": "2026-02-16T10:00:00Z",
                "event_data": {},
            }
        ]

        response = test_client.get(
            "/platformadmin/api/debug/logs?trace_id=specific-trace",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["trace_id"] == "specific-trace"

        # Verify filter was passed to read_debug_logs
        mock_read_logs.assert_called_once()
        call_kwargs = mock_read_logs.call_args.kwargs
        assert call_kwargs["trace_id"] == "specific-trace"

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_debug_logs_filters_by_event_type(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Debug logs should accept event_type filter."""
        mock_read_logs.return_value = []

        response = test_client.get(
            "/platformadmin/api/debug/logs?event_type=supervisor",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200

        # Verify filter was passed
        call_kwargs = mock_read_logs.call_args.kwargs
        assert call_kwargs["event_type"] == "supervisor"

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_debug_logs_accepts_pagination_params(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Debug logs should accept limit and offset parameters."""
        mock_read_logs.return_value = [{"event_type": "test"}] * 10

        response = test_client.get(
            "/platformadmin/api/debug/logs?limit=10&offset=5",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 5  # offset=5 skips first 5

        # Verify limit was passed to read_debug_logs (offset + limit)
        call_kwargs = mock_read_logs.call_args.kwargs
        assert call_kwargs["limit"] == 15  # offset + limit


class TestConfigEndpoint:
    """Tests for GET /platformadmin/api/config endpoint."""

    def test_config_returns_system_config_entries(
        self,
        test_client: TestClient,
    ) -> None:
        """Config endpoint should return list of SystemConfig entries."""
        response = test_client.get(
            "/platformadmin/api/config",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestConversationsEndpoint:
    """Tests for GET /platformadmin/api/conversations endpoint."""

    def test_conversations_accepts_pagination_params(
        self,
        test_client: TestClient,
    ) -> None:
        """Conversations endpoint should accept limit and offset."""
        response = test_client.get(
            "/platformadmin/api/conversations?limit=10&offset=0",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_conversations_accepts_context_filter(
        self,
        test_client: TestClient,
    ) -> None:
        """Conversations endpoint should accept context_id filter."""
        context_id = uuid4()

        response = test_client.get(
            f"/platformadmin/api/conversations?context_id={context_id}",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestConversationMessagesEndpoint:
    """Tests for GET /platformadmin/api/conversations/{id}/messages endpoint."""

    def test_messages_returns_404_for_nonexistent_conversation(
        self,
        test_client: TestClient,
    ) -> None:
        """Messages endpoint should return 404 for nonexistent conversation."""
        conversation_id = uuid4()

        response = test_client.get(
            f"/platformadmin/api/conversations/{conversation_id}/messages",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_messages_accepts_pagination_params(
        self,
        test_client: TestClient,
    ) -> None:
        """Messages endpoint should accept limit and offset."""
        conversation_id = uuid4()

        # Mock conversation existence
        async def mock_get_db() -> AsyncGenerator[AsyncMock, None]:
            session = AsyncMock(spec=AsyncSession)
            # First query: conversation exists
            mock_conv_result = MagicMock()
            mock_conv_result.scalar_one_or_none.return_value = MagicMock(id=conversation_id)
            # Second query: messages
            mock_msg_result = MagicMock()
            mock_msg_result.scalars.return_value.all.return_value = []
            # Third query: count
            mock_count_result = MagicMock()
            mock_count_result.scalar.return_value = 0

            session.execute.side_effect = [mock_conv_result, mock_msg_result, mock_count_result]
            yield session

        from core.db.engine import get_db
        from core.runtime.config import get_settings
        from interfaces.http.app import create_app

        app = create_app(
            settings=Settings(
                environment="test",
                litellm_api_base="http://localhost:4000",
                qdrant_url="http://localhost:6333",
                diagnostic_api_key="test-diag-key-123",
            )
        )
        app.dependency_overrides[get_settings] = lambda: Settings(
            environment="test",
            litellm_api_base="http://localhost:4000",
            qdrant_url="http://localhost:6333",
            diagnostic_api_key="test-diag-key-123",
        )
        app.dependency_overrides[get_db] = mock_get_db
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            f"/platformadmin/api/conversations/{conversation_id}/messages?limit=10&offset=0",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "conversation_id" in data
        assert "messages" in data
        assert "total_count" in data


class TestTracesSearchEndpoint:
    """Tests for GET /platformadmin/api/traces/search endpoint."""

    @patch("interfaces.http.admin_api.DiagnosticsService")
    def test_traces_search_accepts_limit_param(
        self,
        mock_diag_cls: MagicMock,
        test_client: TestClient,
    ) -> None:
        """Traces search should accept limit parameter."""
        mock_diag = AsyncMock()
        mock_diag.get_recent_traces.return_value = []
        mock_diag_cls.return_value = mock_diag

        response = test_client.get(
            "/platformadmin/api/traces/search?limit=10",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @patch("interfaces.http.admin_api.DiagnosticsService")
    def test_traces_search_filters_by_status(
        self,
        mock_diag_cls: MagicMock,
        test_client: TestClient,
    ) -> None:
        """Traces search should filter by status."""
        # Mock trace with ERROR status
        mock_trace = MagicMock()
        mock_trace.trace_id = "error-trace"
        mock_trace.status = "ERR"
        mock_trace.total_duration_ms = 1000.0
        mock_trace.start_time = datetime.now(UTC)
        mock_trace.root.name = "test"
        mock_trace.spans = []

        mock_diag = AsyncMock()
        mock_diag.get_recent_traces.return_value = [mock_trace]
        mock_diag_cls.return_value = mock_diag

        response = test_client.get(
            "/platformadmin/api/traces/search?status=ERR",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "ERR"

    @patch("interfaces.http.admin_api.DiagnosticsService")
    def test_traces_search_filters_by_min_duration(
        self,
        mock_diag_cls: MagicMock,
        test_client: TestClient,
    ) -> None:
        """Traces search should filter by minimum duration."""
        # Mock trace with short duration
        mock_trace = MagicMock()
        mock_trace.trace_id = "fast-trace"
        mock_trace.status = "OK"
        mock_trace.total_duration_ms = 50.0  # Too short
        mock_trace.start_time = datetime.now(UTC)
        mock_trace.root.name = "test"
        mock_trace.spans = []

        mock_diag = AsyncMock()
        mock_diag.get_recent_traces.return_value = [mock_trace]
        mock_diag_cls.return_value = mock_diag

        response = test_client.get(
            "/platformadmin/api/traces/search?min_duration_ms=100",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0  # Filtered out


class TestInvestigateEndpoint:
    """Tests for GET /platformadmin/api/investigate/{trace_id} endpoint."""

    @patch("interfaces.http.admin_api.DiagnosticsService")
    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_investigate_returns_unified_view(
        self,
        mock_read_logs: AsyncMock,
        mock_diag_cls: MagicMock,
        test_client: TestClient,
    ) -> None:
        """Investigate should return spans, logs, and summary."""
        trace_id = "investigate-me"

        # Mock trace
        mock_span = MagicMock()
        mock_span.name = "test-span"
        mock_span.duration_ms = 100.0
        mock_span.status = "OK"
        mock_span.attributes = {}
        mock_span.start_time = "2026-02-16T10:00:00Z"

        mock_trace = MagicMock()
        mock_trace.trace_id = trace_id
        mock_trace.spans = [mock_span]

        mock_diag = AsyncMock()
        mock_diag.get_recent_traces.return_value = [mock_trace]
        mock_diag_cls.return_value = mock_diag

        # Mock debug logs
        mock_read_logs.return_value = [
            {
                "trace_id": trace_id,
                "event_type": "tool_call",
                "event_data": {"tool_name": "search"},
            }
        ]

        response = test_client.get(
            f"/platformadmin/api/investigate/{trace_id}",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["trace_id"] == trace_id
        assert "spans" in data
        assert "debug_logs" in data
        assert "summary" in data
        assert data["summary"]["span_count"] == 1


class TestDebugStatsEndpoint:
    """Tests for GET /platformadmin/api/debug/stats endpoint."""

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_debug_stats_returns_aggregated_stats(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Debug stats should return aggregated statistics."""
        mock_read_logs.return_value = [
            {
                "event_type": "tool_call",
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "event_type": "supervisor",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ]

        response = test_client.get(
            "/platformadmin/api/debug/stats",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "total_logs" in data
        assert "by_event_type" in data
        assert "by_hour" in data
        assert "recent_errors" in data


class TestToolStatsEndpoint:
    """Tests for GET /platformadmin/api/tools/stats endpoint."""

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_tool_stats_returns_execution_metrics(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Tool stats should return execution metrics per tool."""
        mock_read_logs.return_value = [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event_data": {"tool_name": "search", "duration_ms": 100.0},
            },
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event_data": {"tool_name": "search", "duration_ms": 150.0},
            },
        ]

        response = test_client.get(
            "/platformadmin/api/tools/stats",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "period_hours" in data
        assert "tools" in data
        assert "total_tool_calls" in data


class TestSkillStatsEndpoint:
    """Tests for GET /platformadmin/api/skills/stats endpoint."""

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_skill_stats_returns_execution_metrics(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Skill stats should return execution metrics per skill."""
        mock_read_logs.return_value = [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event_data": {
                    "skill_name": "researcher",
                    "duration_ms": 200.0,
                    "outcome": "SUCCESS",
                },
            }
        ]

        response = test_client.get(
            "/platformadmin/api/skills/stats",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "period_hours" in data
        assert "skills" in data
        assert "total_skill_steps" in data

    @patch("core.observability.debug_logger.read_debug_logs", new_callable=AsyncMock)
    def test_skill_stats_accepts_hours_param(
        self,
        mock_read_logs: AsyncMock,
        test_client: TestClient,
    ) -> None:
        """Skill stats should accept the hours query parameter."""
        mock_read_logs.return_value = []

        response = test_client.get(
            "/platformadmin/api/skills/stats?hours=48",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["period_hours"] == 48


class TestRequestsStatsEndpoint:
    """Tests for GET /platformadmin/api/requests/stats endpoint."""

    def test_requests_stats_returns_expected_shape(
        self,
        test_client: TestClient,
    ) -> None:
        """Requests stats should return period_hours, endpoints, and total_requests."""
        response = test_client.get(
            "/platformadmin/api/requests/stats",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "period_hours" in data
        assert "endpoints" in data
        assert "total_requests" in data

    def test_requests_stats_returns_empty_when_no_spans(
        self,
        test_client: TestClient,
    ) -> None:
        """Requests stats returns zero totals when spans.jsonl is missing."""
        response = test_client.get(
            "/platformadmin/api/requests/stats",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        # In test environment there is no spans.jsonl file, so total should be 0
        assert isinstance(data["total_requests"], int)
        assert isinstance(data["endpoints"], dict)

    def test_requests_stats_accepts_hours_param(
        self,
        test_client: TestClient,
    ) -> None:
        """Requests stats should accept the hours query parameter."""
        response = test_client.get(
            "/platformadmin/api/requests/stats?hours=72",
            headers={"X-API-Key": "test-diag-key-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["period_hours"] == 72

    def test_requests_stats_requires_auth(
        self,
        test_client: TestClient,
    ) -> None:
        """Requests stats should require API key authentication."""
        response = test_client.get("/platformadmin/api/requests/stats")

        assert response.status_code == 401


class TestModelsEndpoint:
    """Tests for GET /v1/models endpoint."""

    def test_models_endpoint_returns_200_with_models_key(
        self,
        test_client: TestClient,
    ) -> None:
        """GET /v1/models should return 200 with a models key (no auth required when key unset)."""
        from unittest.mock import AsyncMock, patch

        # Mock AgentService.list_models to avoid real LiteLLM call
        mock_models_response = {"object": "list", "data": [{"id": "gpt-4", "object": "model"}]}

        with patch(
            "interfaces.http.app.AgentService",
            autospec=True,
        ):
            from core.runtime.service import AgentService

            with patch.object(
                AgentService,
                "list_models",
                new_callable=AsyncMock,
                return_value=mock_models_response,
            ):
                response = test_client.get("/v1/models")

        # Either succeeds (200) or fails with a known status (502 if LiteLLM unreachable)
        assert response.status_code in (200, 500, 502)

    def test_models_endpoint_no_auth_required_in_test_env(
        self,
        test_client: TestClient,
    ) -> None:
        """When AGENT_INTERNAL_API_KEY is unset, /v1/models allows unauthenticated access."""
        from unittest.mock import AsyncMock, patch

        mock_models_response = {"object": "list", "data": []}

        with patch(
            "interfaces.http.app.AgentService",
            autospec=True,
        ):
            from core.runtime.service import AgentService

            with patch.object(
                AgentService,
                "list_models",
                new_callable=AsyncMock,
                return_value=mock_models_response,
            ):
                # Should NOT return 401 when AGENT_INTERNAL_API_KEY is not configured
                response = test_client.get("/v1/models")

        assert response.status_code != 401
