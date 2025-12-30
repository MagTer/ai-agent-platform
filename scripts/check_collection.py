import asyncio
import os
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root / "services" / "agent" / "src"))

from qdrant_client import AsyncQdrantClient  # noqa: E402


async def main():
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    client = AsyncQdrantClient(url=qdrant_url)
    collection_name = "tibp-wiki"

    print(f"Checking {collection_name} at {qdrant_url}...")
    try:
        exists = await client.collection_exists(collection_name)
        if exists:
            print("EXISTS")
        else:
            print("MISSING - Creating...")
            from qdrant_client.http import models

            await client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
            )
            print("CREATED")
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"ERROR REPR: {repr(e)}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
