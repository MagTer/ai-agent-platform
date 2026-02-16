import hashlib
import logging
import os
from pathlib import Path

import pathspec
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from core.protocols import IEmbedder
from modules.indexer.code_splitter import CodeSplitter

logger = logging.getLogger(__name__)


class CodeIndexer:
    def __init__(
        self,
        root_path: Path,
        embedder: IEmbedder,
        qdrant_url: str = "http://qdrant:6333",
        collection_name: str = "agent-memories",
    ):
        self.root_path = root_path
        self.client = AsyncQdrantClient(url=qdrant_url)
        self.collection_name = collection_name
        self.embedder = embedder
        self.splitter = CodeSplitter()

    def _get_gitignore_spec(self) -> pathspec.PathSpec | None:
        gitignore_path = self.root_path / ".gitignore"
        if gitignore_path.exists():
            with open(gitignore_path, encoding="utf-8") as f:
                return pathspec.PathSpec.from_lines("gitwildmatch", f)
        return None

    def _calculate_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def _file_needs_update(self, file_path: Path, content_hash: str) -> bool:
        """
        Check if the file needs updating by comparing hash with existing record in Qdrant.
        We optimize by retrieving just one chunk for this file and checking its 'file_hash'.
        """
        # Filter by filepath
        filter_condition = models.Filter(
            must=[
                models.FieldCondition(
                    key="filepath",
                    match=models.MatchValue(value=str(file_path)),
                )
            ]
        )

        # Scroll to get 1 record
        res, _ = await self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=filter_condition,
            limit=1,
            with_payload=True,
            with_vectors=False,
        )

        if not res:
            return True  # No record exists

        # If record exists, check hash
        payload = res[0].payload
        if not payload:
            return True
        existing_hash = payload.get("file_hash")
        return existing_hash != content_hash

    async def _delete_old_chunks(self, file_path: Path) -> None:
        """
        Delete all chunks associated with this file before re-indexing.
        """
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="filepath",
                            match=models.MatchValue(value=str(file_path)),
                        )
                    ]
                )
            ),
        )

    async def index_file(self, file_path: Path) -> None:
        try:
            # Read Content
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            logger.warning(f"Skipping binary or non-utf8 file: {file_path}")
            return
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
            return

        content_hash = self._calculate_hash(content)

        # Check if update needed
        if not await self._file_needs_update(file_path, content_hash):
            logger.debug(f"Skipping unchanged file: {file_path}")
            return

        logger.info(f"Indexing changed file: {file_path}")

        # Delete old chunks
        await self._delete_old_chunks(file_path)

        # Split
        chunks = self.splitter.split_file(file_path, content)
        if not chunks:
            return

        # Embed
        texts = [c["text"] for c in chunks]
        vectors = await self.embedder.embed(texts)

        # Prepare Points
        points = []
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            # Add extra metadata
            payload = chunk
            payload["file_hash"] = content_hash
            payload["source"] = "codebase"

            # Create a deterministic ID based on hash + index to allow partial overwrites if needed,
            # but we are deleting old chunks anyway so random UUID or hash-based UUID is fine.
            # Using hash of content + index for stability.
            point_id = hashlib.sha256((content_hash + str(i)).encode()).hexdigest()

            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            )

        # Upsert
        if points:
            await self.client.upsert(
                collection_name=self.collection_name,
                points=points,
            )
            logger.info(f"Indexed {len(points)} chunks for {file_path}")

    async def scan_and_index(self) -> None:
        spec = self._get_gitignore_spec()

        # Always exclude .git, .venv, __pycache__
        # If no gitignore, we should ideally have some defaults

        for root, dirs, files in os.walk(self.root_path):
            root_path = Path(root)

            # Modify dirs in-place to skip hidden/ignored directories
            # (Basic check)
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "node_modules", ".venv", "env")
            ]

            for file in files:
                if file.startswith("."):
                    continue

                file_path = root_path / file
                rel_path = file_path.relative_to(self.root_path)

                if spec and spec.match_file(rel_path):
                    continue

                if file_path.suffix not in (
                    ".py",
                    ".md",
                    ".txt",
                    ".yml",
                    ".yaml",
                    ".json",
                    ".toml",
                ):
                    # Limit to text files we care about for now
                    continue

                await self.index_file(file_path)

    async def close(self) -> None:
        """Close the Qdrant client connection if initialized."""
        if hasattr(self, "client") and self.client is not None:
            await self.client.close()
