import asyncio

from qdrant_client import AsyncQdrantClient

# print(f"Qdrant Client Version: {qdrant_client.__version__}")


async def main():
    client = AsyncQdrantClient(url="http://qdrant:6333")
    print(f"Client type: {type(client)}")
    methods = [m for m in dir(client) if "search" in m or "query" in m]
    print(f"Search/Query methods: {methods}")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
