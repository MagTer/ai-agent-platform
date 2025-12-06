"""Pydantic models for Model Context Protocol (MCP) data structures."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class McpTool(BaseModel):
    """Represents a tool exposed by an MCP server."""

    name: str = Field(..., description="The unique name of the tool.")
    description: str = Field(..., description="A description of what the tool does.")
    input_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON schema for the tool's input parameters."
    )
    # Additional fields might be needed depending on the full MCP spec
    # For now, this is a minimal representation.


__all__ = ["McpTool"]
