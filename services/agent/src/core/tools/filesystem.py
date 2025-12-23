"""Filesystem tools for safe local file access."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .base import Tool

LOGGER = logging.getLogger(__name__)


def validate_path(base_path: str | Path, requested_path: str) -> Path:
    """Resolve and validate that requested_path is within base_path.

    Args:
        base_path: The root directory that bounds access.
        requested_path: The relative or absolute path requested.

    Returns:
        The resolved absolute Path object.

    Raises:
        ValueError: If the path is outside the base_path sandbox.
    """
    base = Path(base_path).resolve()
    # Force the requested path to be relative to avoid absolute path restarts
    clean_req = str(requested_path).strip()

    # Use os.path.join and normpath to handle '..' rigorously
    joined = os.path.join(str(base), clean_req)
    normalized = os.path.normpath(joined)
    full_target = Path(normalized).resolve()

    # Check for path traversal relative to base
    if not full_target.is_relative_to(base):
        raise ValueError(f"Access denied: Path '{requested_path}' is outside the sandbox.")

    return full_target


class ListDirectoryTool(Tool):
    """List files and directories in a given path."""

    name = "list_directory"
    description = (
        "List files and directories in the specified path. "
        "Args: path (str, optional) - Relative path to list (defaults to root)."
    )

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path

    async def run(self, path: str = ".", **kwargs: Any) -> str:
        try:
            target = validate_path(self._base_path, path)
        except ValueError as exc:
            return f"Error: {exc}"

        if not target.exists():
            return f"Error: Path '{path}' does not exist."
        if not target.is_dir():
            return f"Error: Path '{path}' is not a directory."

        try:
            items = sorted(os.listdir(target))
            if not items:
                return "(empty directory)"

            # Format output clearly
            output = [f"Contents of '{path}':"]
            for item in items:
                full_item = target / item
                suffix = "/" if full_item.is_dir() else ""
                output.append(f"- {item}{suffix}")
            return "\n".join(output)
        except PermissionError:
            return f"Error: Permission denied listing '{path}'."
        except Exception as exc:
            return f"Error: Failed to list directory: {exc}"


class ReadFileTool(Tool):
    """Read specific file contents."""

    name = "read_file"
    description = "Read the contents of a file. " "Args: path (str) - Relative path to the file."

    def __init__(self, base_path: str, max_length: int = 10000) -> None:
        self._base_path = base_path
        self._max_length = max_length

    async def run(self, path: str, **kwargs: Any) -> str:
        try:
            target = validate_path(self._base_path, path)
        except ValueError as exc:
            return f"Error: {exc}"

        if not target.exists():
            return f"Error: File '{path}' does not exist."
        if not target.is_file():
            return f"Error: Path '{path}' is not a file."

        try:
            # Enforce UTF-8 reading
            text = target.read_text(encoding="utf-8")
            if len(text) > self._max_length:
                truncated = text[: self._max_length]
                return f"{truncated}\n...[Content Truncated at {self._max_length} chars]"
            return text
        except UnicodeDecodeError:
            return f"Error: File '{path}' is not valid UTF-8 text."
        except PermissionError:
            return f"Error: Permission denied reading '{path}'."
        except Exception as exc:
            return f"Error: Failed to read file: {exc}"


class EditFileTool(Tool):
    """Smart search-and-replace tool for surgical edits."""

    name = "edit_file"
    description = (
        "Replace a specific block of text in a file with new content. "
        "Args: "
        "path (str) - File path. "
        "target (str) - Exact text block to replace. "
        "replacement (str) - New text content."
    )

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path

    async def run(self, path: str, target: str, replacement: str, **kwargs: Any) -> str:
        try:
            target_path = validate_path(self._base_path, path)
        except ValueError as exc:
            return f"Error: {exc}"

        if not target_path.exists():
            return f"Error: File '{path}' does not exist."
        if not target_path.is_file():
            return f"Error: Path '{path}' is not a file."

        try:
            content = target_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: File '{path}' is not valid UTF-8 text."
        except Exception as exc:
            return f"Error: Failed to read file: {exc}"

        # Normalize line endings for reliable matching?
        # For now, start strict. If target has \r\n and file has \n, it might fail.
        # Let's simple count occurrences.
        count = content.count(target)

        if count == 0:
            # Fallback: Try ignoring trailing whitespace on lines?
            # For strict mode requested: Return Error.
            return (
                "Error: Target block not found in file. "
                "Ensure exact match including whitespace and indentation."
            )

        if count > 1:
            return (
                f"Error: Target block found {count} times. "
                "Provide a more unique target block (add surrounding lines)."
            )

        # Single match - safer to use replace
        new_content = content.replace(target, replacement, 1)

        try:
            target_path.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            return f"Error: Failed to write to file: {exc}"

        return f"Success: File '{path}' updated."
