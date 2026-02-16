"""Ingest TIBP Wiki documents into Qdrant."""

import argparse
import asyncio
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root / "services" / "agent" / "src"))

from colorama import Fore, Style, init  # noqa: E402
from qdrant_client.http import models  # noqa: E402

from core.runtime.config import Settings  # noqa: E402
from core.runtime.litellm_client import LiteLLMClient  # noqa: E402
from modules.embedder import LiteLLMEmbedder  # noqa: E402
from modules.rag import RAGManager  # noqa: E402

init(autoreset=True)

DATA_DIR = project_root / "services" / "agent" / "data" / "tibp_wiki_export"
COLLECTION_NAME = "tibp-wiki"


async def main(force: bool = False):
    print(f"{Style.BRIGHT}🚀 Starting TIBP Wiki Ingestion...")

    # 1. Initialize LiteLLM embedder and RAG
    settings = Settings()
    client = LiteLLMClient(settings)
    embedder = LiteLLMEmbedder(client)

    rag = RAGManager(
        embedder=embedder,
        qdrant_url=str(settings.qdrant_url),
        collection_name=COLLECTION_NAME,
    )

    try:
        print(f"📦 Checking collection '{COLLECTION_NAME}'...")
        try:
            exists = await rag.client.collection_exists(COLLECTION_NAME)

            if exists and not force:
                print(
                    f"   {Fore.YELLOW}⚠️  Collection '{COLLECTION_NAME}' exists. "
                    "Use --force to recreate."
                )
                return

            if exists and force:
                print(f"   {Fore.YELLOW}Deleting existing collection '{COLLECTION_NAME}'...")
                await rag.client.delete_collection(COLLECTION_NAME)

            print("   Creating new collection with 4096-dim vectors and HNSW config...")
            await rag.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=4096,  # qwen3-embedding-8b
                    distance=models.Distance.COSINE,
                ),
                hnsw_config=models.HnswConfigDiff(
                    m=32,
                    ef_construct=256,
                ),
            )
            print(f"   {Fore.GREEN}✓ Collection created successfully.")

        except Exception as e:
            print(f"   {Fore.RED}❌ Could not check/create collection: {e}")
            raise

        # 3. Read Files
        if not DATA_DIR.exists():
            print(f"{Fore.RED}❌ Data directory {DATA_DIR} not found.")
            return

        files = list(DATA_DIR.glob("**/*.md")) + list(DATA_DIR.glob("**/*.txt"))
        if not files:
            print(f"{Fore.YELLOW}⚠️  No .md or .txt files found in {DATA_DIR}")
            return

        print(f"📂 Found {len(files)} documents.")

        # 4. Ingest with updated chunk size (~2000 chars ≈ 512 tokens)
        total_chunks = 0
        for f in files:
            print(f"   Processing {f.name}...")
            content = f.read_text(encoding="utf-8")
            if not content.strip():
                continue

            metadata = {
                "uri": f.name,
                "filepath": str(f.relative_to(project_root)),
                "source": "tibp_wiki",
                "type": "documentation",
            }

            n = await rag.ingest_document(
                content,
                metadata,
                chunk_size=2000,  # ~512 tokens
                chunk_overlap=300,
            )
            print(f"     -> Added {n} chunks.")
            total_chunks += n

        print(f"\n{Fore.GREEN}✅ Ingestion Complete! Total Chunks: {total_chunks}")

    except Exception as e:
        print(f"{Fore.RED}❌ Error: {e}")
        raise
    finally:
        print(f"\n{Style.DIM}Cleaning up connections...")
        await rag.close()
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest TIBP Wiki into Qdrant")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and recreate collection if it exists",
    )
    args = parser.parse_args()

    asyncio.run(main(force=args.force))
