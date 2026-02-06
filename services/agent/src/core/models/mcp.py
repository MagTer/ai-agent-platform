"""Pydantic models for Model Context Protocol (MCP) data structures."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class McpToolAnnotations(BaseModel):
    """Tool annotations from MCP spec 2025-03-26+.

    Hints that describe tool behavior to clients without affecting model context.
    """

    read_only_hint: bool | None = Field(default=None, alias="readOnlyHint")
    destructive_hint: bool | None = Field(default=None, alias="destructiveHint")
    idempotent_hint: bool | None = Field(default=None, alias="idempotentHint")
    open_world_hint: bool | None = Field(default=None, alias="openWorldHint")

    model_config = {"populate_by_name": True}


class McpTool(BaseModel):
    """Represents a tool exposed by an MCP server."""

    name: str = Field(..., description="The unique name of the tool.")
    description: str = Field(default="", description="A description of what the tool does.")
    input_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON schema for the tool's input parameters."
    )
    annotations: McpToolAnnotations | None = Field(
        default=None, description="Tool behavior annotations (spec 2025-03-26+)."
    )
    output_schema: dict[str, Any] | None = Field(
        default=None,
        alias="outputSchema",
        description="JSON schema for structured tool output.",
    )

    model_config = {"populate_by_name": True}


class McpResource(BaseModel):
    """Represents a resource exposed by an MCP server."""

    uri: str = Field(..., description="Unique URI for the resource.")
    name: str = Field(..., description="Human-readable name.")
    description: str = Field(default="", description="Resource description.")
    mime_type: str | None = Field(
        default=None, alias="mimeType", description="MIME type of the resource."
    )

    model_config = {"populate_by_name": True}


class McpPrompt(BaseModel):
    """Represents a prompt template exposed by an MCP server."""

    name: str = Field(..., description="Unique name of the prompt.")
    description: str = Field(default="", description="Prompt description.")
    arguments: list[dict[str, Any]] = Field(
        default_factory=list, description="Arguments the prompt accepts."
    )


__all__ = ["McpTool", "McpToolAnnotations", "McpResource", "McpPrompt"]
