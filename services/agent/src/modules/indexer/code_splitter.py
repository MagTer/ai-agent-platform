from pathlib import Path
from typing import Any

from langchain_text_splitters import (
    Language,
    RecursiveCharacterTextSplitter,
)


class CodeSplitter:
    """
    Splits code files into chunks while preserving semantic structure where possible.
    """

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # Default fallback splitter
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        # Python specific splitter (using LangChain's optimized logic)
        self.python_splitter = RecursiveCharacterTextSplitter.from_language(
            language=Language.PYTHON, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

    def split_file(self, file_path: Path, content: str) -> list[dict[str, Any]]:
        """
        Splits the file content into chunks.
        Returns a list of dicts with: text, metadata (filepath, start_line, etc.)
        """
        extension = file_path.suffix.lower()
        splitter = self.recursive_splitter

        if extension == ".py":
            splitter = self.python_splitter

        # Create chunks
        # LangChain docs usually return 'page_content' and 'metadata'
        docs = splitter.create_documents([content], metadatas=[{"filepath": str(file_path)}])

        chunks = []
        for doc in docs:
            chunk_data = {
                "text": doc.page_content,
                "filepath": str(file_path),
                "type": "code_chunk",
                # We can add more metadata if we parse it, but for now this is good
            }
            chunks.append(chunk_data)

        return chunks
