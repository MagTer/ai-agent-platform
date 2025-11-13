"""Configuration management for the agent service."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

# Ensure .env values are loaded before settings initialisation.
load_dotenv()

DEFAULT_LITELLM_API_BASE: HttpUrl = cast(HttpUrl, "http://litellm:4000")
DEFAULT_QDRANT_URL: HttpUrl = cast(HttpUrl, "http://qdrant:6333")
DEFAULT_WEBFETCH_URL: HttpUrl = cast(HttpUrl, "http://webfetch:8081")
DEFAULT_EMBEDDER_URL: HttpUrl = cast(HttpUrl, "http://embedder:8082")


class Settings(BaseModel):
    """Application settings loaded from environment variables."""

    ENV_PREFIX: ClassVar[str] = "AGENT_"

    model_config = ConfigDict(extra="ignore")

    app_name: str = Field(default="AI Agent Server", description="Human friendly service name.")
    environment: Literal["development", "production", "test"] = Field(
        default="development",
        description="Runtime environment used for logging and diagnostics.",
    )
    host: str = Field(default="0.0.0.0", description="Host interface for the FastAPI server.")
    port: int = Field(default=8000, description="Listening port for the FastAPI server.")

    litellm_api_base: HttpUrl = Field(
        default=DEFAULT_LITELLM_API_BASE,
        description="Base URL for the LiteLLM gateway.",
    )
    litellm_api_key: str | None = Field(default=None, description="Optional LiteLLM API key.")
    litellm_model: str = Field(
        default="ollama/phi3:mini",
        description="Default model identifier passed to LiteLLM.",
    )
    litellm_timeout: float = Field(
        default=180.0,
        description="Timeout in seconds when calling LiteLLM's `/v1/chat/completions` endpoint.",
    )

    qdrant_url: HttpUrl = Field(
        default=DEFAULT_QDRANT_URL,
        description="Base URL for the Qdrant vector database.",
    )
    qdrant_api_key: str | None = Field(default=None, description="Optional Qdrant API key.")
    qdrant_collection: str = Field(
        default="agent-memories",
        description="Vector collection used to persist semantic memories.",
    )
    qdrant_vector_size: int = Field(
        default=384,
        description="Vector dimensionality used when creating the Qdrant collection.",
    )

    sqlite_state_path: Path = Field(
        default=Path("data/agent_state.sqlite"),
        description="Path to the SQLite database used for metadata.",
    )

    webfetch_url: HttpUrl = Field(
        default=DEFAULT_WEBFETCH_URL,
        description="Internal URL for the fetcher microservice used by tools.",
    )
    embedder_url: HttpUrl = Field(
        default=DEFAULT_EMBEDDER_URL,
        description="Internal URL for the embedder service used by memory.",
    )

    tools_config_path: Path = Field(
        default=Path("config/tools.yaml"),
        description="Path to the YAML registry that declares available tools.",
    )
    tool_result_max_chars: int = Field(
        default=2000,
        description="Maximum number of characters to inject from tool outputs into LLM prompts.",
    )

    log_level: str = Field(default="INFO", description="Python logging level for the service.")

    def __init__(self, **data: Any) -> None:  # noqa: D401 - inherited docstring
        env_values = type(self)._load_environment_values()
        env_values.update(data)
        super().__init__(**env_values)

    @classmethod
    def _load_environment_values(cls) -> dict[str, Any]:
        """Return field values sourced from the current environment."""

        values: dict[str, Any] = {}
        for field_name in cls.model_fields:
            env_key = f"{cls.ENV_PREFIX}{field_name.upper()}"
            if env_key in os.environ:
                values[field_name] = os.environ[env_key]
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a singleton instance of :class:`Settings`."""

    return Settings()


__all__ = ["Settings", "get_settings"]
