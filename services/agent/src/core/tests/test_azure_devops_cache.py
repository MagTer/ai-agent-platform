"""Tests for Azure DevOps connection caching."""

import time
from collections.abc import Generator
from uuid import uuid4

import pytest

from core.tools.azure_devops import (
    CONNECTION_CACHE_MAX_SIZE,
    CONNECTION_CACHE_TTL,
    _connection_cache,
    _evict_expired_connections,
    _get_or_create_connection,
)


@pytest.fixture(autouse=True)
def clear_cache() -> Generator[None, None, None]:
    """Clear connection cache before and after each test."""
    _connection_cache.clear()
    yield
    _connection_cache.clear()


class TestConnectionCaching:
    """Test connection caching behavior."""

    def test_cache_stores_new_connection(self) -> None:
        """Test that new connections are cached."""
        context_id = uuid4()
        org_url = "https://dev.azure.com/TestOrg"
        pat = "test_pat_token"

        # First call creates connection
        conn1 = _get_or_create_connection(context_id, org_url, pat)

        # Verify it's in cache
        cache_key = f"{context_id}:{org_url}"
        assert cache_key in _connection_cache
        assert len(_connection_cache) == 1

        # Second call returns cached connection
        conn2 = _get_or_create_connection(context_id, org_url, pat)

        # Should be the same object
        assert conn1 is conn2
        assert len(_connection_cache) == 1

    def test_cache_isolates_by_context_and_org(self) -> None:
        """Test that cache isolates by context_id and org_url."""
        context1 = uuid4()
        context2 = uuid4()
        org_url = "https://dev.azure.com/TestOrg"
        pat = "test_pat"

        conn1 = _get_or_create_connection(context1, org_url, pat)
        conn2 = _get_or_create_connection(context2, org_url, pat)

        # Should be different connections
        assert conn1 is not conn2
        assert len(_connection_cache) == 2

    def test_cache_respects_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that expired connections are recreated."""
        context_id = uuid4()
        org_url = "https://dev.azure.com/TestOrg"
        pat = "test_pat"

        # Create initial connection
        conn1 = _get_or_create_connection(context_id, org_url, pat)
        cache_key = f"{context_id}:{org_url}"

        # Manually expire the cache entry
        _, created_at = _connection_cache[cache_key]
        _connection_cache[cache_key] = (conn1, created_at - CONNECTION_CACHE_TTL - 10)

        # Next call should create new connection
        conn2 = _get_or_create_connection(context_id, org_url, pat)

        # Should be different objects (new connection created)
        assert conn1 is not conn2
        assert len(_connection_cache) == 1

    def test_eviction_removes_expired_entries(self) -> None:
        """Test that expired entries are removed by eviction."""
        now = time.time()

        # Add some expired entries
        for i in range(5):
            context_id = uuid4()
            cache_key = f"{context_id}:https://dev.azure.com/Org{i}"
            # Mock connection (we don't actually create Connection objects here)
            _connection_cache[cache_key] = (None, now - CONNECTION_CACHE_TTL - 10)

        # Add some fresh entries
        for i in range(3):
            context_id = uuid4()
            cache_key = f"{context_id}:https://dev.azure.com/Fresh{i}"
            _connection_cache[cache_key] = (None, now)

        assert len(_connection_cache) == 8

        # Run eviction
        _evict_expired_connections()

        # Only fresh entries should remain
        assert len(_connection_cache) == 3

    def test_eviction_respects_max_size(self) -> None:
        """Test that eviction removes oldest entries when max size exceeded."""
        now = time.time()

        # Add more entries than max size (all fresh)
        for i in range(CONNECTION_CACHE_MAX_SIZE + 10):
            context_id = uuid4()
            cache_key = f"{context_id}:https://dev.azure.com/Org{i}"
            # Stagger timestamps so we can verify oldest-first eviction
            _connection_cache[cache_key] = (None, now + i)

        # Run eviction
        _evict_expired_connections()

        # Should be at max size
        assert len(_connection_cache) == CONNECTION_CACHE_MAX_SIZE

        # Verify that newest entries remain (higher timestamps)
        remaining_timestamps = [created_at for _, created_at in _connection_cache.values()]
        # All remaining should be from the newer batch
        assert all(ts >= now + 10 for ts in remaining_timestamps)

    def test_different_orgs_same_context_creates_separate_cache(self) -> None:
        """Test that different org URLs for same context are cached separately."""
        context_id = uuid4()
        org1 = "https://dev.azure.com/Org1"
        org2 = "https://dev.azure.com/Org2"
        pat = "test_pat"

        conn1 = _get_or_create_connection(context_id, org1, pat)
        conn2 = _get_or_create_connection(context_id, org2, pat)

        # Should have 2 cache entries
        assert len(_connection_cache) == 2
        assert conn1 is not conn2
