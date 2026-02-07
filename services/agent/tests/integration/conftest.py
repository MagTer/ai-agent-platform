"""Pytest fixtures for core tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.db.models import Base


@pytest_asyncio.fixture
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an async database session for testing.

    Uses an in-memory SQLite database for fast, isolated tests.
    """
    # Use in-memory SQLite database
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session factory
    async_session_maker = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    # Provide session
    async with async_session_maker() as session:
        yield session

    # Cleanup
    await engine.dispose()
