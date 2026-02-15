"""CouchDB HTTP API client for Obsidian LiveSync vault access."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CouchDBClient:
    """Async CouchDB client for LiveSync vault operations.

    Obsidian LiveSync stores notes as CouchDB documents:
    {
      "_id": "path/to/note.md",
      "_rev": "3-abc123",
      "data": "# Note Title\n\nMarkdown content...",
      "mtime": 1707000000000,
      "ctime": 1706000000000,
      "size": 1234,
      "type": "plain"
    }
    """

    def __init__(self, url: str, db: str, user: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.db = db
        self.auth = (user, password)

    async def search(
        self, query: str, path_filter: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Full-text search via CouchDB _find with regex on data field.

        Args:
            query: Search term (regex pattern)
            path_filter: Optional path prefix filter (e.g. "Projects/")
            limit: Max results (default 20)

        Returns:
            List of matching documents with _id, data, mtime
        """
        selector: dict[str, Any] = {
            "data": {"$regex": f"(?i){query}"},  # Case-insensitive
        }

        if path_filter:
            selector["_id"] = {"$regex": f"^{path_filter}"}

        find_query = {
            "selector": selector,
            "limit": limit,
            "fields": ["_id", "data", "mtime", "ctime"],
        }

        async with httpx.AsyncClient(auth=self.auth, timeout=30.0) as client:
            response = await client.post(
                f"{self.url}/{self.db}/_find",
                json=find_query,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            result = response.json()
            return result.get("docs", [])

    async def read(self, path: str) -> dict[str, Any] | None:
        """Fetch document by _id (path).

        Args:
            path: Document ID (file path in vault)

        Returns:
            Document dict or None if not found
        """
        async with httpx.AsyncClient(auth=self.auth, timeout=30.0) as client:
            response = await client.get(f"{self.url}/{self.db}/{path}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def list_docs(
        self, path: str | None = None, recursive: bool = False
    ) -> list[dict[str, str]]:
        """List documents via CouchDB _all_docs with prefix filtering.

        Args:
            path: Optional path prefix (e.g. "Projects/")
            recursive: If True, list all nested docs; if False, list direct children only

        Returns:
            List of dicts with "id" and "key" fields
        """
        params: dict[str, Any] = {}

        if path:
            params["startkey"] = f'"{path}"'
            # End key trick: prefix + \ufff0 matches all docs starting with prefix
            params["endkey"] = f'"{path}\ufff0"'

        async with httpx.AsyncClient(auth=self.auth, timeout=30.0) as client:
            response = await client.get(f"{self.url}/{self.db}/_all_docs", params=params)
            response.raise_for_status()
            result = response.json()

            docs = []
            for row in result.get("rows", []):
                doc_id = row.get("id", "")
                # Skip design docs
                if doc_id.startswith("_design/"):
                    continue

                # If not recursive and path specified, filter to direct children only
                if path and not recursive:
                    relative = doc_id[len(path) :]
                    if "/" in relative:
                        continue  # Skip nested docs

                docs.append({"id": doc_id, "key": row.get("key", doc_id)})

            return docs

    async def write(self, path: str, content: str) -> dict[str, Any]:
        """Write document to CouchDB.

        Args:
            path: Document ID (file path in vault)
            content: Markdown content

        Returns:
            CouchDB response with "ok", "id", "rev"

        Raises:
            ValueError: If path doesn't start with _ai-platform/
        """
        if not path.startswith("_ai-platform/"):
            raise ValueError(f"Write rejected: path must start with _ai-platform/ (got: {path})")

        # Check if doc exists (need _rev for update)
        existing = await self.read(path)

        doc = {
            "_id": path,
            "data": content,
            "type": "plain",
            "ctime": existing["ctime"] if existing else None,
            "mtime": None,  # CouchDB will set server timestamp
            "size": len(content),
        }

        if existing:
            doc["_rev"] = existing["_rev"]

        async with httpx.AsyncClient(auth=self.auth, timeout=30.0) as client:
            response = await client.put(
                f"{self.url}/{self.db}/{path}",
                json=doc,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()
