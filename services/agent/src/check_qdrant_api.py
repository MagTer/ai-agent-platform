import asyncio

from qdrant_client import AsyncQdrantClient


async def main():
    print("Inspecting AsyncQdrantClient...")
    client = AsyncQdrantClient(location=":memory:")
    methods = [m for m in dir(client) if not m.startswith("_")]
    print(f"Available methods: {methods}")

    if "search" in methods:
        print("SUCCESS: 'search' method found.")
    else:
        print("FAILURE: 'search' method NOT found.")

    if "query_points" in methods:
        print("INFO: 'query_points' method found.")


if __name__ == "__main__":
    asyncio.run(main())
