import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
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
    """Multi-tenant context representing a user workspace.

    Each context isolates conversations, OAuth tokens, and tool permissions.
    Contexts enable multi-user support and workspace separation.

    Attributes:
        id: Unique context identifier.
        name: Human-readable name (unique).
        type: Context type (git_repo, devops, etc.).
        config: JSON configuration specific to context type.
        pinned_files: List of file paths to inject into prompts.
        default_cwd: Default working directory for tool execution.
    """

    __tablename__ = "contexts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String)  # e.g. 'git_repo', 'devops'
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    pinned_files: Mapped[list[str]] = mapped_column(JSONB, default=list)
    default_cwd: Mapped[str] = mapped_column(String, default="/tmp")  # noqa: S108

    conversations = relationship(
        "Conversation", back_populates="context", cascade="all, delete-orphan"
    )
    oauth_tokens = relationship("OAuthToken", cascade="all, delete-orphan")
    tool_permissions = relationship("ToolPermission", cascade="all, delete-orphan")
    scheduled_jobs = relationship("ScheduledJob", cascade="all, delete-orphan")


class Conversation(Base):
    """Chat conversation thread linked to a platform and context.

    Tracks conversation metadata, working directory state, and links
    to the parent context for multi-tenancy.

    Attributes:
        id: Unique conversation identifier.
        platform: Platform identifier (openwebui, api, etc.).
        platform_id: Platform-specific conversation ID.
        context_id: Parent context for isolation.
        current_cwd: Current working directory for tool execution.
        conversation_metadata: JSON metadata (pending HITL state, etc.).
        created_at: Conversation creation timestamp.
        updated_at: Last update timestamp.
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String)
    platform_id: Mapped[str] = mapped_column(String)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    current_cwd: Mapped[str] = mapped_column(String)
    conversation_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    context = relationship("Context", back_populates="conversations")
    sessions = relationship("Session", back_populates="conversation", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_conversation_platform_lookup", "platform", "platform_id"),)


class Session(Base):
    """Agent execution session within a conversation.

    Groups messages for a single agent request/response cycle.
    Sessions enable request-level isolation and metadata tracking.

    Attributes:
        id: Unique session identifier.
        conversation_id: Parent conversation.
        active: Whether session is currently active.
        session_metadata: JSON metadata for session state.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Note: 'metadata' is reserved in SQLAlchemy Base, using 'meta_data' or 'session_metadata'
    # But usually mapped_column should handle it if passed as name
    session_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    conversation = relationship("Conversation", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    """Individual chat message in a session.

    Stores message content, role, and observability metadata.

    Attributes:
        id: Unique message identifier.
        session_id: Parent session.
        role: Message role (user, assistant, system, tool).
        content: Message text content.
        created_at: Message timestamp (indexed for sorting).
        trace_id: OpenTelemetry trace ID for debugging.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String)  # user, assistant, system, tool
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    session = relationship("Session", back_populates="messages")

    __table_args__ = (Index("ix_message_session_created", "session_id", "created_at"),)


class User(Base):
    """User account linked to Open WebUI identity."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)  # Primary identifier
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, default="user")  # "user" or "admin"
    # Open WebUI's internal ID
    openwebui_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)
    active_context_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contexts.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    user_contexts = relationship("UserContext", back_populates="user", cascade="all, delete-orphan")


class UserContext(Base):
    """Junction table linking users to contexts with role."""

    __tablename__ = "user_contexts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String, default="owner")  # "owner", "member", "viewer"
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # User's personal context
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

    # Relationships
    user = relationship("User", back_populates="user_contexts")
    context = relationship("Context")

    __table_args__ = (UniqueConstraint("user_id", "context_id", name="uq_user_context"),)


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


class UserCredential(Base):
    """Encrypted credential storage per context."""

    __tablename__ = "user_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    credential_type: Mapped[str] = mapped_column(String, index=True)
    encrypted_value: Mapped[str] = mapped_column(String)  # Fernet encrypted
    # Non-sensitive metadata (org URL, etc.)
    credential_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    context = relationship("Context")

    __table_args__ = (
        UniqueConstraint("context_id", "credential_type", name="uq_context_credential_type"),
    )


class HomeyDeviceCache(Base):
    """Cached Homey device metadata for fast lookups.

    Caches device names and capabilities to avoid API calls.
    TTL: 36 hours, refreshed nightly at 03:00 UTC.
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


class Workspace(Base):
    """Git repository workspace per context.

    Tracks cloned repositories for code investigation and automated fixes.
    Each context can have multiple workspaces (repos).

    Attributes:
        id: Primary key
        context_id: Foreign key to Context (multi-tenant isolation)
        name: Human-friendly workspace name (e.g., "backend-api")
        repo_url: Git repository URL (HTTPS)
        branch: Current branch name
        local_path: Absolute path to cloned repo on disk
        status: Current status (cloned, syncing, error, deleted)
        last_synced_at: Last successful git pull/clone timestamp
        sync_error: Last sync error message (if any)
        workspace_metadata: Additional data (commit SHA, remote info, etc.)
        created_at: Workspace creation timestamp
        updated_at: Last update timestamp
    """

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String, index=True)
    repo_url: Mapped[str] = mapped_column(String)
    branch: Mapped[str] = mapped_column(String, default="main")
    local_path: Mapped[str] = mapped_column(String)
    # Status: pending, cloned, syncing, error
    status: Mapped[str] = mapped_column(String, default="pending")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    context = relationship("Context")

    __table_args__ = (
        UniqueConstraint("context_id", "name", name="uq_context_workspace_name"),
        UniqueConstraint("context_id", "repo_url", name="uq_context_workspace_repo"),
    )


class McpServer(Base):
    """User-defined MCP server connection configuration.

    Stores connection details for Model Context Protocol servers that users
    configure via the admin portal. Each server is scoped to a context
    for multi-tenant isolation.

    Auth types:
    - none: No authentication
    - bearer: Static API key / bearer token (encrypted)
    - oauth: Full OAuth 2.0 flow (uses OAuthToken table)
    """

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str] = mapped_column(String)
    transport: Mapped[str] = mapped_column(String, default="auto")  # auto, sse, streamable_http
    auth_type: Mapped[str] = mapped_column(String, default="none")  # none, bearer, oauth
    auth_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_provider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_authorize_url: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_token_url: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_client_id: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_client_secret_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_scopes: Mapped[str | None] = mapped_column(String, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # pending, connected, error, disabled
    status: Mapped[str] = mapped_column(String, default="pending")
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tools_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    context = relationship("Context")

    __table_args__ = (UniqueConstraint("context_id", "name", name="uq_context_mcp_name"),)

    def set_auth_token(self, plaintext: str | None) -> None:
        """Encrypt and store a static bearer token."""
        if plaintext is None:
            self.auth_token_encrypted = None
        else:
            from core.db.oauth_models import encrypt_token

            self.auth_token_encrypted = encrypt_token(plaintext)

    def get_auth_token(self) -> str | None:
        """Decrypt the stored bearer token."""
        if self.auth_token_encrypted is None:
            return None
        from core.db.oauth_models import decrypt_token

        return decrypt_token(self.auth_token_encrypted)

    def set_oauth_client_secret(self, plaintext: str | None) -> None:
        """Encrypt and store OAuth client secret."""
        if plaintext is None:
            self.oauth_client_secret_encrypted = None
        else:
            from core.db.oauth_models import encrypt_token

            self.oauth_client_secret_encrypted = encrypt_token(plaintext)

    def get_oauth_client_secret(self) -> str | None:
        """Decrypt the stored OAuth client secret."""
        if self.oauth_client_secret_encrypted is None:
            return None
        from core.db.oauth_models import decrypt_token

        return decrypt_token(self.oauth_client_secret_encrypted)


class ScheduledJob(Base):
    """Cron-scheduled job definition scoped to a context.

    Each job triggers a skill execution via AgentService at the
    configured cron schedule. Results are stored in conversation
    history and optionally sent via notification channel.

    Attributes:
        id: Unique job identifier.
        context_id: Parent context for multi-tenant isolation.
        name: Human-readable job name (unique per context).
        description: Optional description of what the job does.
        cron_expression: Standard 5-field cron expression (minute hour day month weekday).
        skill_prompt: The prompt to send to AgentService (e.g., "Check server status").
        is_enabled: Whether the job is active.
        status: Current status (active, paused, error).
        notification_channel: Optional notification channel (telegram, email, none).
        notification_target: Channel-specific target (chat_id for telegram, email for email).
        last_run_at: Timestamp of last execution.
        last_run_status: Status of last execution (success, error).
        last_run_result: Summary of last execution result.
        last_run_duration_ms: Duration of last execution in milliseconds.
        next_run_at: Computed next execution time.
        run_count: Total number of executions.
        error_count: Total number of failed executions.
        max_retries: Number of retries on failure (default 0).
        timeout_seconds: Maximum execution time before timeout (default 300).
        created_at: Job creation timestamp.
        updated_at: Last update timestamp.
    """

    __tablename__ = "scheduled_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    cron_expression: Mapped[str] = mapped_column(String)
    skill_prompt: Mapped[str] = mapped_column(String)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # active, paused, error, running
    status: Mapped[str] = mapped_column(String, default="active")
    # telegram, email, none
    notification_channel: Mapped[str | None] = mapped_column(String, nullable=True)
    notification_target: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String, nullable=True)  # success, error
    last_run_result: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    context = relationship("Context", overlaps="scheduled_jobs")

    __table_args__ = (UniqueConstraint("context_id", "name", name="uq_context_scheduled_job_name"),)


class SystemConfig(Base):
    """Global system configuration stored in database.

    Key-value store for system-wide settings like debug mode.
    Settings are cached in memory with TTL for performance.
    """

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class AdoTeamConfig(Base):
    """Global ADO team mapping configuration stored in database.

    One row has is_default=TRUE, alias=NULL -- this is the global defaults row.
    All other rows have is_default=FALSE and a non-null alias representing a team.

    Replaces the ado_mappings.yaml file with a DB-backed admin portal.
    """

    __tablename__ = "ado_team_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NULL only for the global-defaults row (is_default=TRUE)
    alias: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    area_path: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    # "Feature", "User Story", "Bug"
    default_type: Mapped[str] = mapped_column(String, nullable=False)
    default_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # TRUE = global defaults row (alias=NULL)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class WikiImport(Base):
    """Tracks Azure DevOps wiki import state per context."""

    __tablename__ = "wiki_imports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    wiki_identifier: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="idle")
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    pages_imported: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_import_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_import_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    context = relationship("Context")

    __table_args__ = (
        UniqueConstraint("context_id", "wiki_identifier", name="uq_context_wiki_import"),
    )
