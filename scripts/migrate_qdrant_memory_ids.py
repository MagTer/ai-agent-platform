"""One-off migration script to regenerate Qdrant point IDs for memory records."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList, PointStruct


def _batched(iterable: Iterable[str | int], size: int) -> Iterator[list[str | int]]:
    batch: list[str | int] = []
    for item in iterable:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def migrate_point_ids(
    url: str, api_key: str | None, collection: str, batch_size: int
) -> int:
    """Re-write all points in *collection* with freshly generated UUID identifiers."""

    client = QdrantClient(url=url, api_key=api_key)
    offset: int | None = None
    processed = 0

    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=batch_size,
            with_payload=True,
            with_vectors=True,
            offset=offset,
        )

        if not points:
            break

        new_points: list[PointStruct] = []
        old_point_ids: list[str | int] = []

        for point in points:
            new_points.append(
                PointStruct(
                    id=uuid4().hex, payload=point.payload or {}, vector=point.vector
                )
            )
            old_point_ids.append(point.id)

        client.upsert(collection_name=collection, points=new_points)

        for batch in _batched(old_point_ids, batch_size):
            client.delete(
                collection_name=collection, points_selector=PointIdsList(points=batch)
            )

        processed += len(points)

    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default="http://localhost:6333", help="Qdrant service URL"
    )
    parser.add_argument("--api-key", default=None, help="Optional Qdrant API key")
    parser.add_argument(
        "--collection",
        default="agent-memories",
        help="Qdrant collection storing agent semantic memories",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Number of points to migrate per request",
    )

    args = parser.parse_args()
    migrated = migrate_point_ids(
        args.url, args.api_key, args.collection, args.batch_size
    )
    print(f"Migrated {migrated} points in collection '{args.collection}'.")


if __name__ == "__main__":
    main()
