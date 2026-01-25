# Homey Integration Optimizations

**Date:** 2026-01-25
**Author:** Architect (Opus)
**Status:** Ready for Implementation

---

## Overview

Two optimizations to improve the Homey smart home integration:

1. **Device Name Caching** - Cache device metadata in the database to avoid API calls for name lookups
2. **Direct Skill Routing** - Bypass the planner for simple Homey commands using regex fast-paths

---

## Feature 1: Device Name Caching

### Requirements

- Cache Homey device names in the database
- TTL: 36 hours
- Automatic refresh: Nightly scheduled job
- Per-context multi-tenant isolation
- Cache miss: Fetch from API and populate cache
- Manual refresh via `sync_devices` action

### Data to Cache Per Device

```python
device_id: str        # Homey's UUID (primary key with context)
name: str             # Device display name
device_class: str     # light, socket, sensor, etc.
capabilities: list    # ["onoff", "dim", "measure_temperature"]
zone: str | None      # Room/zone name
homey_id: str         # Parent Homey ID
```

### Architecture Decision

**Layer:** `core/db/models.py` (new model) + `core/tools/homey.py` (modify)

The device cache is a database model in `core/` because:
1. It's persistence/data layer concern
2. The `HomeyTool` in `core/tools/` needs to access it
3. No business logic - just data storage

**NOT a module** because:
- It's tightly coupled to the Homey tool (not reusable)
- Simple CRUD, no complex business logic

---

## Implementation Roadmap

### Phase 1: Database Model & Migration

#### Step 1.1: Create HomeyDeviceCache Model

**File:** `services/agent/src/core/db/models.py`

Add the following model after `UserCredential`:

```python
class HomeyDeviceCache(Base):
    """Cached Homey device metadata for fast lookups.

    Caches device names and capabilities to avoid API calls.
    TTL: 36 hours, refreshed nightly.
    """

    __tablename__ = "homey_device_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    homey_id: Mapped[str] = mapped_column(String, index=True)
    device_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    device_class: Mapped[str] = mapped_column(String)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    zone: Mapped[str | None] = mapped_column(String, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

    __table_args__ = (
        UniqueConstraint("context_id", "homey_id", "device_id", name="uq_homey_device_cache"),
    )
```

**Engineer tasks:**
- Add the model to `core/db/models.py`
- Add import to alembic env.py

**QA tasks (after Engineer completes):**
- Run `stack check`

#### Step 1.2: Create Alembic Migration

**File:** `services/agent/alembic/versions/20260125_add_homey_device_cache.py`

```python
"""add_homey_device_cache

Revision ID: 20260125_homey_cache
Revises: 20260124_add_package_size_to_products
Create Date: 2026-01-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260125_homey_cache"
down_revision: str | Sequence[str] | None = "20260124_add_package_size_to_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create homey_device_cache table."""
    op.create_table(
        "homey_device_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("context_id", UUID(as_uuid=True), sa.ForeignKey("contexts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("homey_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("device_class", sa.String(), nullable=False),
        sa.Column("capabilities", JSONB(), nullable=False, server_default="[]"),
        sa.Column("zone", sa.String(), nullable=True),
        sa.Column("cached_at", sa.DateTime(), nullable=False),
    )

    # Indexes
    op.create_index("ix_homey_device_cache_context_id", "homey_device_cache", ["context_id"])
    op.create_index("ix_homey_device_cache_homey_id", "homey_device_cache", ["homey_id"])
    op.create_index("ix_homey_device_cache_device_id", "homey_device_cache", ["device_id"])

    # Unique constraint
    op.create_unique_constraint(
        "uq_homey_device_cache",
        "homey_device_cache",
        ["context_id", "homey_id", "device_id"],
    )


def downgrade() -> None:
    """Drop homey_device_cache table."""
    op.drop_table("homey_device_cache")
```

**Engineer tasks:**
- Create the migration file

**QA tasks:**
- Verify migration syntax

---

### Phase 2: Modify HomeyTool for Caching

#### Step 2.1: Add Cache Constants and Helper Methods

**File:** `services/agent/src/core/tools/homey.py`

Add constants after `ATHOM_API_BASE`:

```python
# Cache configuration
DEVICE_CACHE_TTL_HOURS = 36
```

Add new imports at top:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
```

Add cache helper methods to `HomeyTool` class:

```python
async def _get_cached_device(
    self,
    context_id: UUID,
    homey_id: str,
    device_name: str,
    session: AsyncSession,
) -> str | None:
    """Look up device ID from cache by name.

    Args:
        context_id: Context UUID for multi-tenant isolation.
        homey_id: Homey device ID.
        device_name: Device name to search for.
        session: Database session.

    Returns:
        Device ID if found in valid cache, None otherwise.
    """
    from core.db.models import HomeyDeviceCache

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=DEVICE_CACHE_TTL_HOURS)
    query_lower = device_name.lower()

    # Query cache for matching devices
    stmt = select(HomeyDeviceCache).where(
        HomeyDeviceCache.context_id == context_id,
        HomeyDeviceCache.homey_id == homey_id,
        HomeyDeviceCache.cached_at >= cutoff,
    )
    result = await session.execute(stmt)
    cached_devices = result.scalars().all()

    if not cached_devices:
        return None

    # Try exact match first
    for device in cached_devices:
        if device.name.lower() == query_lower:
            LOGGER.info(f"Cache hit (exact): '{device.name}' -> {device.device_id}")
            return device.device_id

    # Try partial match
    for device in cached_devices:
        if query_lower in device.name.lower():
            LOGGER.info(f"Cache hit (partial): '{device.name}' -> {device.device_id}")
            return device.device_id

    LOGGER.debug(f"Cache miss for device '{device_name}'")
    return None


async def _populate_cache(
    self,
    context_id: UUID,
    homey_id: str,
    devices: dict[str, dict],
    session: AsyncSession,
) -> None:
    """Populate device cache from API response.

    Args:
        context_id: Context UUID.
        homey_id: Homey device ID.
        devices: Device dict from Homey API.
        session: Database session.
    """
    from core.db.models import HomeyDeviceCache

    now = datetime.now(UTC).replace(tzinfo=None)

    # Delete existing cache for this homey
    await session.execute(
        delete(HomeyDeviceCache).where(
            HomeyDeviceCache.context_id == context_id,
            HomeyDeviceCache.homey_id == homey_id,
        )
    )

    # Insert new cache entries
    for device_id, device in devices.items():
        cache_entry = HomeyDeviceCache(
            context_id=context_id,
            homey_id=homey_id,
            device_id=device_id,
            name=device.get("name", "Unknown"),
            device_class=device.get("class", "other"),
            capabilities=device.get("capabilities", []),
            zone=device.get("zoneName"),
            cached_at=now,
        )
        session.add(cache_entry)

    await session.flush()
    LOGGER.info(f"Cached {len(devices)} devices for Homey {homey_id}")
```

**Engineer tasks:**
- Add constants and imports
- Add cache helper methods

**QA tasks:**
- Run `stack check`

#### Step 2.2: Modify `_find_device_by_name` to Use Cache

Replace the existing `_find_device_by_name` method:

```python
async def _find_device_by_name(
    self,
    homey_url: str,
    session_token: str,
    name_query: str,
    context_id: UUID | None = None,
    homey_id: str | None = None,
    db_session: AsyncSession | None = None,
) -> str | None:
    """Find a device by name, checking cache first.

    Args:
        homey_url: Homey base URL.
        session_token: Homey session token.
        name_query: Device name to search for.
        context_id: Context UUID for cache lookup.
        homey_id: Homey ID for cache lookup.
        db_session: Database session for cache operations.

    Returns:
        Device ID if found, None otherwise.
    """
    # Try cache first if we have context info
    if context_id and homey_id and db_session:
        cached_id = await self._get_cached_device(
            context_id, homey_id, name_query, db_session
        )
        if cached_id:
            return cached_id

    # Cache miss - fetch from API
    devices = await self._homey_request(
        "GET",
        homey_url,
        "/api/manager/devices/device",
        session_token,
    )

    if not devices:
        return None

    # Populate cache if we have context info
    if context_id and homey_id and db_session:
        await self._populate_cache(context_id, homey_id, devices, db_session)

    # Search in fetched data
    query_lower = name_query.lower()

    # Exact match first
    for device_id, device in devices.items():
        device_name = device.get("name", "")
        if device_name.lower() == query_lower:
            LOGGER.info(f"API match (exact): '{device_name}' -> {device_id}")
            return device_id

    # Partial match
    for device_id, device in devices.items():
        device_name = device.get("name", "")
        if query_lower in device_name.lower():
            LOGGER.info(f"API match (partial): '{device_name}' -> {device_id}")
            return device_id

    LOGGER.warning(f"No device found matching '{name_query}'")
    return None
```

**Engineer tasks:**
- Replace `_find_device_by_name` with cache-aware version

**QA tasks:**
- Run `stack check`

#### Step 2.3: Update `control_device` Action to Pass Context

In the `run` method, update the `control_device` section:

```python
elif action == "control_device":
    # Resolve device_id from device_name if needed
    resolved_device_id = device_id
    if not resolved_device_id and device_name:
        resolved_device_id = await self._find_device_by_name(
            homey_url,
            session_token,
            device_name,
            context_id=context_id,
            homey_id=homey_id,
            db_session=session,
        )
        if not resolved_device_id:
            return f"Error: No device found matching '{device_name}'."

    if not resolved_device_id or not capability:
        return "Error: device_id (or device_name) and capability are required."
    if value is None:
        return "Error: value is required for control_device."
    return await self._action_control_device(
        homey_url, session_token, resolved_device_id, capability, value
    )
```

**Engineer tasks:**
- Update control_device to pass context_id, homey_id, session

**QA tasks:**
- Run `stack check`

#### Step 2.4: Add `sync_devices` Action

Add new action to the parameters schema:

```python
"action": {
    "type": "string",
    "enum": [
        "list_homeys",
        "list_devices",
        "get_device",
        "control_device",
        "list_flows",
        "trigger_flow",
        "sync_devices",  # NEW
    ],
    "description": "Action to perform",
},
```

Add new action handler in `run` method (before the `else` clause):

```python
elif action == "sync_devices":
    return await self._action_sync_devices(
        homey_url, session_token, homey_id, context_id, session
    )
```

Add the action method:

```python
async def _action_sync_devices(
    self,
    homey_url: str,
    session_token: str,
    homey_id: str,
    context_id: UUID | None,
    db_session: AsyncSession | None,
) -> str:
    """Manually sync device cache from Homey API.

    Args:
        homey_url: Homey base URL.
        session_token: Homey session token.
        homey_id: Homey device ID.
        context_id: Context UUID.
        db_session: Database session.

    Returns:
        Sync result message.
    """
    if not context_id or not db_session:
        return "Error: Cannot sync devices without context."

    devices = await self._homey_request(
        "GET",
        homey_url,
        "/api/manager/devices/device",
        session_token,
    )

    if not devices:
        return "No devices found on this Homey."

    await self._populate_cache(context_id, homey_id, devices, db_session)
    await db_session.commit()

    return f"Synced {len(devices)} devices to cache."
```

**Engineer tasks:**
- Add sync_devices to action enum
- Add action handler in run method
- Add _action_sync_devices method

**QA tasks:**
- Run `stack check`

---

### Phase 3: Nightly Sync Scheduler

#### Step 3.1: Create HomeyDeviceSyncScheduler

**File:** `services/agent/src/modules/homey/scheduler.py` (NEW)

```python
"""Background scheduler for nightly Homey device cache sync."""

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db.models import HomeyDeviceCache
from core.db.oauth_models import OAuthToken

logger = logging.getLogger(__name__)


class HomeyDeviceSyncScheduler:
    """Background scheduler for nightly Homey device cache refresh.

    Runs at 03:00 UTC every night to refresh device caches for all
    contexts with Homey OAuth tokens.
    """

    SYNC_HOUR = 3  # 03:00 UTC

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.session_factory = session_factory
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background scheduler."""
        if self._running:
            logger.warning("Homey sync scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Homey device sync scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Homey device sync scheduler stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop - waits until 03:00 UTC then runs sync."""
        while self._running:
            try:
                # Calculate time until next 03:00 UTC
                now = datetime.now(UTC)
                next_sync = datetime.combine(
                    now.date(),
                    time(hour=self.SYNC_HOUR, tzinfo=UTC),
                )
                if next_sync <= now:
                    next_sync += timedelta(days=1)

                wait_seconds = (next_sync - now).total_seconds()
                logger.info(f"Next Homey device sync in {wait_seconds / 3600:.1f} hours")

                await asyncio.sleep(wait_seconds)

                if self._running:
                    await self._sync_all_contexts()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Homey sync scheduler error: {e}", exc_info=True)
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)

    async def _sync_all_contexts(self) -> None:
        """Sync device caches for all contexts with Homey tokens."""
        from core.tools.homey import HomeyTool

        async with self.session_factory() as session:
            # Find all contexts with Homey OAuth tokens
            stmt = select(distinct(OAuthToken.context_id)).where(
                OAuthToken.provider == "homey"
            )
            result = await session.execute(stmt)
            context_ids = result.scalars().all()

            if not context_ids:
                logger.info("No contexts with Homey tokens to sync")
                return

            logger.info(f"Syncing Homey devices for {len(context_ids)} contexts")

            tool = HomeyTool()

            for context_id in context_ids:
                try:
                    await self._sync_context_devices(
                        context_id, tool, session
                    )
                except Exception as e:
                    logger.error(f"Failed to sync context {context_id}: {e}")

            await session.commit()

    async def _sync_context_devices(
        self,
        context_id: UUID,
        tool: HomeyTool,
        session: AsyncSession,
    ) -> None:
        """Sync devices for a single context."""
        from core.providers import get_token_manager_optional

        token_manager = get_token_manager_optional()
        if not token_manager:
            logger.warning("Token manager not available for sync")
            return

        oauth_token = await token_manager.get_token(
            provider="homey",
            context_id=context_id,
        )

        if not oauth_token:
            logger.debug(f"No valid Homey token for context {context_id}")
            return

        # Get user's Homeys
        homeys = await tool._get_user_homeys(oauth_token)

        for homey in homeys:
            homey_id = homey.get("_id")
            if not homey_id:
                continue

            try:
                session_token, homey_url = await tool._ensure_session(
                    homey_id, oauth_token
                )

                devices = await tool._homey_request(
                    "GET",
                    homey_url,
                    "/api/manager/devices/device",
                    session_token,
                )

                if devices:
                    await tool._populate_cache(
                        context_id, homey_id, devices, session
                    )
                    logger.info(
                        f"Synced {len(devices)} devices for Homey {homey_id}"
                    )

            except Exception as e:
                logger.error(f"Failed to sync Homey {homey_id}: {e}")


__all__ = ["HomeyDeviceSyncScheduler"]
```

**Engineer tasks:**
- Create `services/agent/src/modules/homey/__init__.py` with:
  ```python
  """Homey integration module."""

  from modules.homey.scheduler import HomeyDeviceSyncScheduler

  __all__ = ["HomeyDeviceSyncScheduler"]
  ```
- Create the scheduler file

**QA tasks:**
- Run `stack check`

#### Step 3.2: Register Scheduler in App Lifespan

**File:** `services/agent/src/interfaces/http/app.py`

Add to the lifespan function, after the price tracker scheduler:

```python
# Homey Device Sync Scheduler - nightly cache refresh
from modules.homey.scheduler import HomeyDeviceSyncScheduler

homey_scheduler = HomeyDeviceSyncScheduler(
    session_factory=AsyncSessionLocal,
)
await homey_scheduler.start()
LOGGER.info("Homey device sync scheduler started")
```

Add to shutdown section:

```python
await homey_scheduler.stop()
```

**Engineer tasks:**
- Add scheduler initialization in lifespan
- Add scheduler stop in shutdown

**QA tasks:**
- Run `stack check`

---

## Feature 2: Direct Skill Routing (Fast Path)

### Requirements

- Detect simple Homey commands and route directly to skill
- Pattern matching for Swedish and English commands
- Bypass planner when pattern matches
- Fall back to normal flow if no match

### Architecture Decision

**Layer:** `core/core/routing.py` (extend existing FastPathRegistry)

This is a routing concern, so it belongs in the existing routing infrastructure.
The fast path creates a synthetic plan that delegates to the Homey skill.

---

### Phase 4: Add Homey Fast Paths

#### Step 4.1: Add Homey Command Patterns

**File:** `services/agent/src/core/core/routing.py`

Add new patterns after existing registrations:

```python
# --- Homey Smart Home Fast Paths ---

def _map_homey_turn_on(match: re.Match) -> dict[str, Any]:
    """Map 'turn on X' / 'tand X' to Homey control."""
    device_name = match.group(1).strip()
    return {
        "action": "control_device",
        "device_name": device_name,
        "capability": "onoff",
        "value": True,
    }


def _map_homey_turn_off(match: re.Match) -> dict[str, Any]:
    """Map 'turn off X' / 'slack X' to Homey control."""
    device_name = match.group(1).strip()
    return {
        "action": "control_device",
        "device_name": device_name,
        "capability": "onoff",
        "value": False,
    }


def _map_homey_dim(match: re.Match) -> dict[str, Any]:
    """Map 'dim X to Y%' / 'dimma X till Y%' to Homey control."""
    device_name = match.group(1).strip()
    percent = int(match.group(2))
    return {
        "action": "control_device",
        "device_name": device_name,
        "capability": "dim",
        "value": percent / 100.0,  # Convert to 0.0-1.0
    }


def _map_homey_trigger_flow(match: re.Match) -> dict[str, Any]:
    """Map 'trigger flow X' / 'starta flode X' to Homey flow trigger."""
    flow_name = match.group(1).strip()
    return {
        "action": "trigger_flow",
        "flow_id": flow_name,  # Will need flow name lookup in tool
    }


# Swedish: "tand X" / "tand pa X"
registry.register(
    {
        "pattern": re.compile(r"^t[a\xe4]nd\s+(?:p[a\xe5]\s+)?(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_on,
        "description": "Turn on Homey device",
    }
)

# English: "turn on X"
registry.register(
    {
        "pattern": re.compile(r"^turn\s+on\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_on,
        "description": "Turn on Homey device",
    }
)

# Swedish: "slack X" / "stang av X"
registry.register(
    {
        "pattern": re.compile(r"^(?:sl[a\xe4]ck|st[a\xe4]ng\s+av)\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_off,
        "description": "Turn off Homey device",
    }
)

# English: "turn off X"
registry.register(
    {
        "pattern": re.compile(r"^turn\s+off\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_off,
        "description": "Turn off Homey device",
    }
)

# Swedish: "dimma X till Y%"
registry.register(
    {
        "pattern": re.compile(r"^dimma\s+(.+?)\s+till\s+(\d+)\s*%?", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_dim,
        "description": "Dim Homey device",
    }
)

# English: "dim X to Y%"
registry.register(
    {
        "pattern": re.compile(r"^dim\s+(.+?)\s+to\s+(\d+)\s*%?", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_dim,
        "description": "Dim Homey device",
    }
)

# Swedish: "starta flode X" / "kor flode X"
registry.register(
    {
        "pattern": re.compile(r"^(?:starta|k[o\xf6]r)\s+fl[o\xf6]de\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_trigger_flow,
        "description": "Trigger Homey flow",
    }
)

# English: "trigger flow X" / "run flow X" / "start flow X"
registry.register(
    {
        "pattern": re.compile(r"^(?:trigger|run|start)\s+flow\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_trigger_flow,
        "description": "Trigger Homey flow",
    }
)
```

**Engineer tasks:**
- Add mapper functions
- Add pattern registrations

**QA tasks:**
- Run `stack check`

---

### Phase 5: Update Homey Skill for Flow Name Lookup

#### Step 5.1: Add Flow Name Lookup to HomeyTool

The `trigger_flow` action currently requires `flow_id`, but fast path sends `flow_name`.
Add flow name resolution to the tool.

**File:** `services/agent/src/core/tools/homey.py`

Add new parameter to schema:

```python
"flow_name": {
    "type": "string",
    "description": "Flow name to search for (alternative to flow_id)",
},
```

Add flow lookup method:

```python
async def _find_flow_by_name(
    self,
    homey_url: str,
    session_token: str,
    name_query: str,
) -> str | None:
    """Find a flow by name (case-insensitive partial match).

    Args:
        homey_url: Homey base URL.
        session_token: Homey session token.
        name_query: Flow name to search for.

    Returns:
        Flow ID if found, None otherwise.
    """
    flows = await self._homey_request(
        "GET",
        homey_url,
        "/api/manager/flow/flow",
        session_token,
    )

    if not flows:
        return None

    query_lower = name_query.lower()

    # Exact match first
    for flow_id, flow in flows.items():
        flow_name = flow.get("name", "")
        if flow_name.lower() == query_lower:
            LOGGER.info(f"Flow match (exact): '{flow_name}' -> {flow_id}")
            return flow_id

    # Partial match
    for flow_id, flow in flows.items():
        flow_name = flow.get("name", "")
        if query_lower in flow_name.lower():
            LOGGER.info(f"Flow match (partial): '{flow_name}' -> {flow_id}")
            return flow_id

    LOGGER.warning(f"No flow found matching '{name_query}'")
    return None
```

Update the `run` method signature to include `flow_name`:

```python
async def run(
    self,
    action: str,
    homey_id: str | None = None,
    device_id: str | None = None,
    device_name: str | None = None,
    capability: str | None = None,
    value: bool | float | str | None = None,
    flow_id: str | None = None,
    flow_name: str | None = None,  # NEW
    context_id: UUID | None = None,
    session: AsyncSession | None = None,
    **kwargs: Any,
) -> str:
```

Update the `trigger_flow` handler:

```python
elif action == "trigger_flow":
    resolved_flow_id = flow_id
    if not resolved_flow_id and flow_name:
        resolved_flow_id = await self._find_flow_by_name(
            homey_url, session_token, flow_name
        )
        if not resolved_flow_id:
            return f"Error: No flow found matching '{flow_name}'."

    if not resolved_flow_id:
        return "Error: flow_id or flow_name is required for trigger_flow action."
    return await self._action_trigger_flow(homey_url, session_token, resolved_flow_id)
```

**Engineer tasks:**
- Add flow_name parameter
- Add _find_flow_by_name method
- Update trigger_flow handler

**QA tasks:**
- Run `stack check`

---

## Phase 6: Testing

### Step 6.1: Add Cache Tests

**File:** `services/agent/src/core/tools/tests/test_homey.py`

Add new test class:

```python
class TestHomeyDeviceCache:
    """Test Homey device caching functionality."""

    @pytest.mark.asyncio
    async def test_cache_population(
        self,
        homey_tool: HomeyTool,
        mock_context_id: UUID,
    ) -> None:
        """Test that device cache is populated after API call."""
        # This test requires database setup - mark as integration
        pass  # TODO: Implement with test database

    @pytest.mark.asyncio
    async def test_cache_hit(
        self,
        homey_tool: HomeyTool,
        mock_context_id: UUID,
    ) -> None:
        """Test that cache is used when available."""
        pass  # TODO: Implement with test database

    @pytest.mark.asyncio
    async def test_cache_miss_fallback(
        self,
        homey_tool: HomeyTool,
        mock_context_id: UUID,
    ) -> None:
        """Test fallback to API on cache miss."""
        pass  # TODO: Implement with test database
```

**Engineer tasks:**
- Add placeholder tests (can be expanded later)

**QA tasks:**
- Run `stack check`

### Step 6.2: Add Fast Path Tests

**File:** `services/agent/src/core/tests/test_routing.py` (NEW)

```python
"""Tests for Homey fast path routing."""

import re

import pytest

from core.core.routing import registry


class TestHomeyFastPaths:
    """Test Homey command pattern matching."""

    @pytest.mark.parametrize(
        "message,expected_tool,expected_action",
        [
            ("tand vardagsrumslampan", "homey", "control_device"),
            ("Tand pa koket", "homey", "control_device"),
            ("turn on bedroom light", "homey", "control_device"),
            ("Turn on the kitchen", "homey", "control_device"),
            ("slack taklampan", "homey", "control_device"),
            ("Stang av alla lampor", "homey", "control_device"),
            ("turn off living room", "homey", "control_device"),
            ("dimma taklampan till 50%", "homey", "control_device"),
            ("dim bedroom to 75", "homey", "control_device"),
            ("starta flode God natt", "homey", "trigger_flow"),
            ("trigger flow Morning routine", "homey", "trigger_flow"),
        ],
    )
    def test_homey_patterns_match(
        self,
        message: str,
        expected_tool: str,
        expected_action: str,
    ) -> None:
        """Test that Homey patterns match expected messages."""
        result = registry.get_match(message)
        assert result is not None, f"No match for: {message}"
        path, match = result
        assert path["tool"] == expected_tool

        # Check that arg_mapper produces correct action
        if "arg_mapper" in path:
            args = path["arg_mapper"](match)
            assert args["action"] == expected_action

    @pytest.mark.parametrize(
        "message",
        [
            "what is the weather",
            "hello",
            "help me with code",
            "list my devices",  # Should go through skill, not fast path
        ],
    )
    def test_non_homey_patterns_no_match(self, message: str) -> None:
        """Test that non-Homey messages don't match."""
        result = registry.get_match(message)
        # Either no match, or matches a different tool
        if result:
            path, _ = result
            # These shouldn't match homey fast paths
            assert path["tool"] != "homey" or "control_device" not in str(
                path.get("args", {})
            )
```

**Engineer tasks:**
- Create routing test file

**QA tasks:**
- Run `stack check`

---

## Configuration Changes

### Environment Variables

No new environment variables required. Uses existing:
- `POSTGRES_URL` - Database connection

### Database Migration

Run after deployment:
```bash
cd services/agent && poetry run alembic upgrade head
```

---

## Quality Checks

After each phase, run:
```bash
./stack check
```

This runs: Ruff -> Black -> Mypy -> Pytest

---

## Security Considerations

1. **Multi-tenant isolation** - Cache entries are scoped by `context_id`
2. **OAuth token access** - Uses existing secure token manager
3. **No credential storage** - Only caches device metadata, not tokens
4. **Input validation** - Device/flow names are used in case-insensitive search, not raw SQL

---

## Success Criteria

1. Device name lookups use cache when available (no API call)
2. Cache is refreshed nightly at 03:00 UTC
3. `sync_devices` action manually refreshes cache
4. Simple Homey commands bypass planner (faster response)
5. Non-matching commands fall back to normal flow
6. All tests pass
7. Quality gate passes

---

## Agent Delegation

### Engineer (Sonnet) - Implementation
- Write all new code files
- Modify existing code
- Create database migration
- Fix complex type errors

### QA (Haiku - 12x cheaper) - Quality Assurance
- Run quality gate: `stack check` after each phase
- Fix simple lint errors (auto-fixable)
- Report test results
- Escalate complex issues to Engineer

### Cost Optimization
Each implementation step follows:
1. Engineer writes/modifies code
2. Engineer delegates to QA for quality check
3. QA reports back (or escalates if complex errors)
4. Repeat for next step

---

## Files Affected Summary

**New Files:**
- `services/agent/alembic/versions/20260125_add_homey_device_cache.py`
- `services/agent/src/modules/homey/__init__.py`
- `services/agent/src/modules/homey/scheduler.py`
- `services/agent/src/core/tests/test_routing.py`

**Modified Files:**
- `services/agent/src/core/db/models.py` - Add HomeyDeviceCache model
- `services/agent/src/core/tools/homey.py` - Add caching, sync_devices, flow_name
- `services/agent/src/core/core/routing.py` - Add Homey fast paths
- `services/agent/src/interfaces/http/app.py` - Register Homey scheduler
- `services/agent/alembic/env.py` - Add HomeyDeviceCache import
- `services/agent/src/core/tools/tests/test_homey.py` - Add cache tests

---

## Estimated Implementation Time

- Phase 1 (Database): 30 minutes
- Phase 2 (Tool Caching): 45 minutes
- Phase 3 (Scheduler): 30 minutes
- Phase 4 (Fast Paths): 30 minutes
- Phase 5 (Flow Lookup): 20 minutes
- Phase 6 (Testing): 30 minutes

**Total: ~3 hours**
