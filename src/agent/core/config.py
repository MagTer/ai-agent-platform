"""Configuration management for the agent service."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure .env values are loaded before settings initialisation.
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AGENT_",
        extra="ignore",
    )

    app_name: str = Field(default="AI Agent Server", description="Human friendly service name.")
    environment: Literal["development", "production", "test"] = Field(
        default="development",
        description="Runtime environment used for logging and diagnostics.",
    )
    host: str = Field(default="0.0.0.0", description="Host interface for the FastAPI server.")
    port: int = Field(default=8000, description="Listening port for the FastAPI server.")

    litellm_api_base: HttpUrl = Field(
        default="http://litellm:4000", description="Base URL for the LiteLLM gateway.")
    litellm_api_key: str | None = Field(default=None, description="Optional LiteLLM API key.")
    litellm_model: str = Field(
        default="ollama/llama3",
        description="Default model identifier passed to LiteLLM.",
    )

    qdrant_url: HttpUrl = Field(
        default="http://qdrant:6333",
        description="Base URL for the Qdrant vector database.",
    )
    qdrant_api_key: str | None = Field(default=None, description="Optional Qdrant API key.")
    qdrant_collection: str = Field(
        default="agent-memories",
        description="Vector collection used to persist semantic memories.",
    )

    sqlite_state_path: Path = Field(
        default=Path("data/agent_state.sqlite"),
        description="Path to the SQLite database used for metadata.",
    )

    webfetch_url: HttpUrl = Field(
        default="http://webfetch:8081",
        description="Internal URL for the fetcher microservice used by tools.",
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a singleton instance of :class:`Settings`."""

    return Settings()


__all__ = ["Settings", "get_settings"]
