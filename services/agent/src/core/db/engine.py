import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv(
    "POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/agent_db"
)

# Connection pool configuration for production resilience
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,  # Number of connections to keep in the pool
    max_overflow=20,  # Max additional connections beyond pool_size
    pool_recycle=3600,  # Recycle connections after 1 hour (avoid stale connections)
    pool_pre_ping=True,  # Verify connection health before using
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
