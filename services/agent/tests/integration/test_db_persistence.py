import os
import uuid

import pytest
from core.db.models import Base, Context, Conversation, Message
from core.db.models import Session as AgentSession
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Use Postgres for testing (requires docker-compose up postgres)
TEST_DB_URL = os.getenv(
    "POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_db"
)


@pytest.fixture
async def db_session():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_persistence(db_session):
    # 1. Create Context
    context = Context(name="default", type="virtual", default_cwd="/tmp")  # noqa: S108
    db_session.add(context)
    await db_session.flush()

    # 2. Create Conversation
    conv_id = uuid.uuid4()
    conv = Conversation(
        id=conv_id,
        platform="telegram",
        platform_id="123456",
        context_id=context.id,
        current_cwd="/tmp",  # noqa: S108
        conversation_metadata={"test": "data"},
    )
    db_session.add(conv)
    await db_session.commit()

    # 3. Verify
    retrieved = await db_session.get(Conversation, conv_id)
    assert retrieved is not None
    assert retrieved.platform == "telegram"
    assert retrieved.platform_id == "123456"
    assert retrieved.conversation_metadata == {"test": "data"}


@pytest.mark.asyncio
async def test_message_persistence(db_session):
    # Setup Context & Conversation & Session
    context = Context(name="default_msg", type="virtual", default_cwd="/tmp")  # noqa: S108
    db_session.add(context)
    await db_session.flush()

    conv = Conversation(
        platform="web",
        platform_id="session_xyz",
        context_id=context.id,
        current_cwd="/tmp",  # noqa: S108
    )
    db_session.add(conv)
    await db_session.flush()

    sess = AgentSession(conversation_id=conv.id, active=True)
    db_session.add(sess)
    await db_session.flush()

    # Create Message
    msg = Message(session_id=sess.id, role="user", content="Hello World")
    db_session.add(msg)
    await db_session.commit()

    # Verify
    retrieved_msg = await db_session.get(Message, msg.id)
    assert retrieved_msg.content == "Hello World"
    assert retrieved_msg.role == "user"
