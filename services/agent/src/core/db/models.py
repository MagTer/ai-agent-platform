import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utc_now() -> datetime:
    """Return naive UTC datetime for SQLAlchemy defaults.

    Returns naive datetime to match TIMESTAMP WITHOUT TIME ZONE columns.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Context(Base):
    __tablename__ = "contexts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    type: Mapped[str] = mapped_column(String)  # e.g. 'git_repo', 'devops'
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default={})
    pinned_files: Mapped[list[str]] = mapped_column(JSONB, default=list)
    default_cwd: Mapped[str] = mapped_column(String, default="/tmp")  # noqa: S108

    conversations = relationship(
        "Conversation", back_populates="context", cascade="all, delete-orphan"
    )
    oauth_tokens = relationship("OAuthToken", cascade="all, delete-orphan")
    tool_permissions = relationship("ToolPermission", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String)
    platform_id: Mapped[str] = mapped_column(String)
    context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contexts.id"))
    current_cwd: Mapped[str] = mapped_column(String)
    conversation_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default={})
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    context = relationship("Context", back_populates="conversations")
    sessions = relationship("Session", back_populates="conversation", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Note: 'metadata' is reserved in SQLAlchemy Base, using 'meta_data' or 'session_metadata'
    # But usually mapped_column should handle it if passed as name
    session_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default={})

    conversation = relationship("Conversation", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    role: Mapped[str] = mapped_column(String)  # user, assistant, system, tool
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    session = relationship("Session", back_populates="messages")


class ToolPermission(Base):
    """Per-context tool access permissions.

    Controls which tools are available to each context (user/workspace).
    Default behavior is allow-all (if no permission record exists).
    """

    __tablename__ = "tool_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String, index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (UniqueConstraint("context_id", "tool_name", name="uq_context_tool"),)
