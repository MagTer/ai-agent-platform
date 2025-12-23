import asyncio
import logging
import os

from core.db.engine import engine, AsyncSessionLocal
from core.db.models import Base, Context
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

async def init_db():
    LOGGER.info("Creating database tables...")
    try:
        async with engine.begin() as conn:
            # await conn.run_sync(Base.metadata.drop_all) # Optional: don't drop every time if verifying? 
            # But for clean verify let's drop.
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        LOGGER.info("Tables created successfully.")
        
        # Seed Default Context
        async with AsyncSessionLocal() as session:
             stmt = select(Context).where(Context.name == "default")
             result = await session.execute(stmt)
             if not result.scalar_one_or_none():
                 LOGGER.info("Seeding 'default' context...")
                 default_ctx = Context(
                     name="default",
                     type="virtual",
                     config={},
                     default_cwd="/app/contexts/default"
                 )
                 session.add(default_ctx)
                 await session.commit()
                 LOGGER.info("Seeded 'default' context.")
             else:
                 LOGGER.info("'default' context already exists.")

    except Exception as e:
        LOGGER.error(f"Error creating tables: {e}")
        raise
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_db())
