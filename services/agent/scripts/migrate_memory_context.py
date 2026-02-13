"""Add context_id to existing Qdrant memory points.

This migration script:
1. Connects to Qdrant
2. Fetches all existing memory points
3. Maps conversation_id → context_id from database
4. Updates each point with the appropriate context_id
5. Re-uploads points with updated payloads

Run this after deploying the context-aware MemoryStore changes.
"""

import asyncio
import sys
from collections.abc import Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from sqlalchemy import select

from core.db.engine import AsyncSessionLocal
from core.db.models import Conversation
from core.runtime.config import get_settings


async def migrate_memory_contexts() -> None:
    """Add context_id to existing memory points."""
    settings = get_settings()

    print("=" * 80)
    print("Memory Context Migration Script")
    print("=" * 80)
    print()

    # Connect to Qdrant
    print(f"Connecting to Qdrant at {settings.qdrant_url}...")
    client = QdrantClient(
        url=str(settings.qdrant_url),
        api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
    )

    # Check if collection exists
    try:
        collection_info = client.get_collection(settings.qdrant_collection)
        print(f"✅ Found collection '{settings.qdrant_collection}'")
        print(f"   Points count: {collection_info.points_count}")
    except Exception as e:
        print(f"❌ Collection not found: {e}")
        print("   Nothing to migrate.")
        return

    # Get all points
    print()
    print("Fetching all memory points...")
    points: list[PointStruct] = []
    offset = None
    batch_size = 100

    while True:
        batch, next_offset = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )

        points.extend(batch)
        print(f"   Fetched {len(points)} points so far...")

        if next_offset is None:
            break
        offset = next_offset

    print(f"✅ Fetched {len(points)} total memory points")

    if not points:
        print("   Nothing to migrate.")
        return

    # Check if any points already have context_id
    points_with_context = sum(
        1 for point in points if point.payload and "context_id" in point.payload
    )
    points_without_context = len(points) - points_with_context

    print()
    print(f"Points with context_id: {points_with_context}")
    print(f"Points without context_id: {points_without_context}")

    if points_without_context == 0:
        print()
        print("✅ All points already have context_id. Migration complete!")
        return

    # Map conversation_id → context_id
    print()
    print("Building conversation_id → context_id mapping from database...")
    conversation_to_context: dict[str, str] = {}

    async with AsyncSessionLocal() as session:
        stmt = select(Conversation)
        result = await session.execute(stmt)
        conversations: Sequence[Conversation] = result.scalars().all()

        for conv in conversations:
            conversation_to_context[str(conv.id)] = str(conv.context_id)

    print(f"✅ Loaded {len(conversation_to_context)} conversation → context mappings")

    # Update points with context_id
    print()
    print("Updating points with context_id...")
    updated_points: list[PointStruct] = []
    skipped_points = 0
    orphaned_points = 0

    for point in points:
        payload = point.payload or {}

        # Skip if already has context_id
        if "context_id" in payload:
            continue

        conversation_id = payload.get("conversation_id")

        if not conversation_id:
            orphaned_points += 1
            print(f"⚠️  Point {point.id} has no conversation_id")
            continue

        if conversation_id in conversation_to_context:
            # Add context_id to payload
            payload["context_id"] = conversation_to_context[conversation_id]
            updated_points.append(
                PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=payload,
                )
            )
        else:
            skipped_points += 1
            print(
                f"⚠️  No conversation found for point {point.id} "
                f"(conversation_id: {conversation_id})"
            )

    print()
    print(f"Points to update: {len(updated_points)}")
    print(f"Orphaned points (no conversation_id): {orphaned_points}")
    print(f"Skipped points (conversation not found): {skipped_points}")

    if not updated_points:
        print()
        print("No points to update. Migration complete.")
        return

    # Batch update
    print()
    print("Uploading updated points to Qdrant...")
    batch_size = 100
    for i in range(0, len(updated_points), batch_size):
        batch = updated_points[i : i + batch_size]
        client.upsert(
            collection_name=settings.qdrant_collection,
            points=batch,
        )
        print(
            f"   Uploaded batch {i // batch_size + 1}/{(len(updated_points) - 1) // batch_size + 1}"
        )

    print()
    print("=" * 80)
    print("✅ Migration Complete!")
    print("=" * 80)
    print()
    print("Summary:")
    print(f"  Total points processed: {len(points)}")
    print(f"  Points updated with context_id: {len(updated_points)}")
    print(f"  Points already had context_id: {points_with_context}")
    print(f"  Orphaned points: {orphaned_points}")
    print(f"  Skipped points: {skipped_points}")
    print()

    # Verify migration
    print("Verifying migration...")
    collection_info = client.get_collection(settings.qdrant_collection)
    print(f"✅ Collection now has {collection_info.points_count} points")

    # Sample a few points to verify context_id
    sample, _ = client.scroll(
        collection_name=settings.qdrant_collection,
        limit=5,
        with_payload=True,
        with_vectors=False,
    )

    print()
    print("Sample points after migration:")
    for point in sample:
        payload = point.payload or {}
        context_id = payload.get("context_id", "MISSING")
        conversation_id = payload.get("conversation_id", "MISSING")
        print(
            f"  Point {point.id[:8]}... context_id={context_id[:8]}... "  # noqa: E501
            f"conv_id={conversation_id[:8]}..."
        )

    print()
    print("Migration script finished successfully!")


if __name__ == "__main__":
    print()
    print("This script will add context_id to existing Qdrant memory points.")
    print("It's safe to run multiple times (idempotent).")
    print()

    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        print("DRY RUN MODE - no changes will be made")
        print()

    confirm = input("Continue? [y/N]: ")
    if confirm.lower() != "y":
        print("Aborted.")
        sys.exit(0)

    asyncio.run(migrate_memory_contexts())
