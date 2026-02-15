"""FastMCP server exposing Obsidian vault via CouchDB."""

import logging
import os
import sys

from couch_client import CouchDBClient
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Read config from environment
COUCHDB_URL = os.getenv("COUCHDB_URL", "http://localhost:5984")
COUCHDB_DB = os.getenv("COUCHDB_DB", "obsidian")
COUCHDB_USER = os.getenv("COUCHDB_USER", "admin")
COUCHDB_PASSWORD = os.getenv("COUCHDB_PASSWORD", "")

if not COUCHDB_PASSWORD:
    logger.error("COUCHDB_PASSWORD environment variable is required")
    sys.exit(1)

# Initialize CouchDB client
couch = CouchDBClient(COUCHDB_URL, COUCHDB_DB, COUCHDB_USER, COUCHDB_PASSWORD)

# Create FastMCP server
mcp = FastMCP("Obsidian Vault")


@mcp.tool()
async def vault_search(query: str, path_filter: str = "", limit: int = 20) -> str:
    """Search vault for notes matching a query.

    Args:
        query: Search term (supports regex)
        path_filter: Optional path prefix to filter results (e.g. "Projects/")
        limit: Maximum number of results (default 20)

    Returns:
        JSON list of matching notes with path, content preview, and modified time
    """
    try:
        docs = await couch.search(query, path_filter=path_filter or None, limit=limit)

        results = []
        for doc in docs:
            path = doc.get("_id", "")
            data = doc.get("data", "")
            mtime = doc.get("mtime")

            # Preview: first 200 chars
            preview = data[:200] + "..." if len(data) > 200 else data

            results.append({"path": path, "preview": preview, "mtime": mtime, "size": len(data)})

        return f"Found {len(results)} matches:\n{results}"
    except Exception as e:
        logger.exception("vault_search failed")
        return f"Error searching vault: {e}"


@mcp.tool()
async def vault_read(path: str) -> str:
    """Read a note from the vault by its path.

    Args:
        path: Path to the note (e.g. "Projects/MyProject.md")

    Returns:
        Full content of the note
    """
    try:
        doc = await couch.read(path)
        if not doc:
            return f"Note not found: {path}"

        content = doc.get("data", "")
        return content
    except Exception as e:
        logger.exception("vault_read failed")
        return f"Error reading note: {e}"


@mcp.tool()
async def vault_list(path: str = "", recursive: bool = False) -> str:
    """List notes in the vault, optionally filtered by path prefix.

    Args:
        path: Optional path prefix (e.g. "Projects/")
        recursive: If true, list all nested notes; if false, list direct children only

    Returns:
        List of note paths
    """
    try:
        docs = await couch.list_docs(path=path or None, recursive=recursive)

        paths = [doc["id"] for doc in docs]
        return f"Found {len(paths)} notes:\n" + "\n".join(paths)
    except Exception as e:
        logger.exception("vault_list failed")
        return f"Error listing vault: {e}"


@mcp.tool()
async def vault_write(path: str, content: str) -> str:
    """Write a note to the vault (restricted to _ai-platform/ prefix).

    Args:
        path: Path for the note (must start with "_ai-platform/")
        content: Markdown content to write

    Returns:
        Success message with revision ID
    """
    try:
        result = await couch.write(path, content)
        return f"Note written successfully: {path} (rev: {result.get('rev')})"
    except ValueError as e:
        # Path validation error (not _ai-platform/ prefix)
        return f"Write rejected: {e}"
    except Exception as e:
        logger.exception("vault_write failed")
        return f"Error writing note: {e}"


if __name__ == "__main__":
    logger.info(f"Starting vault-mcp server (DB: {COUCHDB_DB} at {COUCHDB_URL})")
    mcp.run(transport="stdio")
