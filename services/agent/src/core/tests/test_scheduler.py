"""Tests for the scheduler service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from interfaces.scheduler.adapter import SchedulerAdapter


class TestComputeNextRun:
    """Test cron expression parsing and next_run computation."""

    def test_every_minute(self) -> None:
        """Test '* * * * *' returns a time within 60 seconds."""
        now = datetime.now(UTC).replace(tzinfo=None)
        next_run = SchedulerAdapter._compute_next_run("* * * * *")
        assert next_run > now
        diff = (next_run - now).total_seconds()
        assert diff <= 60

    def test_daily_at_nine(self) -> None:
        """Test '0 9 * * *' returns a time with hour=9, minute=0."""
        next_run = SchedulerAdapter._compute_next_run("0 9 * * *")
        assert next_run.hour == 9
        assert next_run.minute == 0

    def test_weekdays_only(self) -> None:
        """Test '0 8 * * 1-5' returns a weekday."""
        next_run = SchedulerAdapter._compute_next_run("0 8 * * 1-5")
        assert next_run.weekday() < 5  # 0=Mon, 4=Fri


class TestSchedulerAdapter:
    """Test SchedulerAdapter initialization and control."""

    def test_init(self) -> None:
        """Test adapter initializes with correct defaults."""
        session_factory = MagicMock()
        service_factory = MagicMock()

        adapter = SchedulerAdapter(
            session_factory=session_factory,
            service_factory=service_factory,
        )

        assert adapter.platform_name == "scheduler"
        assert adapter._running is False
        assert adapter._task is None

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """Test adapter start and stop lifecycle."""
        session_factory = MagicMock()
        service_factory = MagicMock()

        adapter = SchedulerAdapter(
            session_factory=session_factory,
            service_factory=service_factory,
        )

        await adapter.start()
        assert adapter._running is True
        assert adapter._task is not None

        await adapter.stop()
        assert adapter._running is False


class TestCronValidation:
    """Test cron expression validation in admin endpoints."""

    def test_valid_cron_expressions(self) -> None:
        """Test that valid cron expressions are accepted."""
        from croniter import croniter

        valid = [
            "* * * * *",
            "0 9 * * *",
            "0 8 * * 1-5",
            "*/15 * * * *",
            "0 0 1 * *",
        ]
        for expr in valid:
            assert croniter.is_valid(expr), f"{expr} should be valid"

    def test_invalid_cron_expressions(self) -> None:
        """Test that invalid cron expressions are rejected."""
        from croniter import croniter

        invalid = [
            "not a cron",
            "* * *",
            "60 * * * *",
            "",
        ]
        for expr in invalid:
            assert not croniter.is_valid(expr), f"{expr} should be invalid"
