"""GitHub API tool for repository analysis."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from .base import Tool

LOGGER = logging.getLogger(__name__)


class GitHubTool(Tool):
    """Interact with GitHub repositories using the public REST API."""

    name = "github_repo"
    description = (
        "Analyze GitHub repositories. "
        "Args: action (str) - One of 'get_readme', 'list_files', 'read_file'. "
        "repo_url (str) - The full URL (e.g., https://github.com/owner/repo). "
        "file_path (str) - Optional path for 'read_file'."
    )

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN")
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "ai-agent-platform",
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
            },
            timeout=30.0,
            follow_redirects=True,
        )

    async def run(
        self,
        action: str,
        repo_url: str,
        file_path: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute the GitHub action."""

        # Normalize URL to owner/repo
        try:
            owner, repo = self._parse_repo_url(repo_url)
        except ValueError as exc:
            return f"Error: {exc}"

        base_api = f"https://api.github.com/repos/{owner}/{repo}"

        try:
            if action == "get_readme":
                return await self._get_readme(base_api)
            elif action == "list_files":
                return await self._list_files(base_api)
            elif action == "read_file":
                if not file_path:
                    return "Error: 'file_path' is required for action='read_file'."
                return await self._read_file(base_api, file_path)
            else:
                return (
                    f"Error: Unknown action '{action}'. "
                    "Supported actions: 'get_readme', 'list_files', 'read_file'."
                )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403 and "rate limit" in exc.response.text.lower():
                return "Error: GitHub API rate limit exceeded. Please try again later or provide a GITHUB_TOKEN."
            if exc.response.status_code == 404:
                return "Error: Resource not found. Check the URL and file path."
            return f"Error: GitHub API request failed ({exc.response.status_code}): {exc.response.text}"
        except Exception as exc:
            LOGGER.exception("GitHub tool error")
            return f"Error: {exc}"

    async def _get_readme(self, base_api: str) -> str:
        resp = await self._client.get(f"{base_api}/readme")
        resp.raise_for_status()
        data = resp.json()
        return self._decode_content(data)

    async def _list_files(self, base_api: str) -> str:
        # Get default branch sha
        resp = await self._client.get(base_api)
        resp.raise_for_status()
        default_branch = resp.json().get("default_branch", "main")

        # Get tree recursively
        tree_url = f"{base_api}/git/trees/{default_branch}?recursive=1"
        resp = await self._client.get(tree_url)
        resp.raise_for_status()
        
        data = resp.json()
        if data.get("truncated"):
             LOGGER.warning("GitHub tree truncated")
        
        files = [
            item["path"] 
            for item in data.get("tree", []) 
            if item["type"] == "blob"
        ]
        
        # Limit output size
        preview = "\n".join(files[:500])
        count = len(files)
        if count > 500:
            preview += f"\n... ({count - 500} more files)"
        
        return f"Files in {base_api}:\n{preview}"

    async def _read_file(self, base_api: str, path: str) -> str:
        url = f"{base_api}/contents/{path}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        
        if isinstance(data, list):
            return f"Error: '{path}' is a directory, not a file. Use 'list_files' to see contents."
            
        return self._decode_content(data)

    def _decode_content(self, data: dict[str, Any]) -> str:
        content = data.get("content", "")
        encoding = data.get("encoding")
        
        if encoding == "base64":
            try:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                return decoded
            except Exception as exc:
                return f"Error decoding base64 content: {exc}"
        
        return content or "(empty file)"

    def _parse_repo_url(self, url: str) -> tuple[str, str]:
        """Extract owner and repo from URL."""
        if "github.com/" not in url:
             raise ValueError("URL must be a valid GitHub URL (e.g., https://github.com/owner/repo)")
        
        parts = url.split("github.com/")[1].split("/")
        if len(parts) < 2:
            raise ValueError("Invalid repository URL format")
            
        return parts[0], parts[1].removesuffix(".git")
