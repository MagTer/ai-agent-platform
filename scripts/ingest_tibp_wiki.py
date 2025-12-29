"""Ingest TIBP Wiki documents into Qdrant."""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root / "services" / "agent" / "src"))

from colorama import Fore, Style, init  # noqa: E402
from qdrant_client.http import models  # noqa: E402

from modules.rag import RAGManager  # noqa: E402

init(autoreset=True)

DATA_DIR = project_root / "services" / "agent" / "data" / "tibp_wiki_export"
COLLECTION_NAME = "tibp-wiki"


async def main():
    print(f"{Style.BRIGHT}🚀 Starting TIBP Wiki Ingestion...")

    # 1. Initialize RAG
    # We must override collection in env or init? RAGManager takes defaults from ENV.
    # But RAGManager.__init__ reads ENV.
    # We can pass specific connection if we modify RAGManager or just hack ENV before init?
    # RAGManager uses `self.collection_name = os.getenv("QDRANT_COLLECTION", ...)`
    # So we set ENV.

    os.environ["QDRANT_COLLECTION"] = COLLECTION_NAME
    rag = RAGManager()

    try:
        print(f"📦 Checking collection '{COLLECTION_NAME}'...")
        try:
            exists = await rag.client.collection_exists(COLLECTION_NAME)
            if not exists:
                print("   Creating new collection...")
                await rag.client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=models.VectorParams(
                        size=384, distance=models.Distance.COSINE  # Default from config
                    ),
                )
            else:
                print("   Collection exists.")
        except Exception as e:
            print(f"   ⚠️ Could not check/create collection: {e}")
            # Continue to try ingesting anyway (maybe just check failed)

        # 3. Read Files
        if not DATA_DIR.exists():
            print(f"{Fore.RED}❌ Data directory {DATA_DIR} not found.")
            return

        files = list(DATA_DIR.glob("**/*.md")) + list(DATA_DIR.glob("**/*.txt"))
        if not files:
            print(f"{Fore.YELLOW}⚠️  No .md or .txt files found in {DATA_DIR}")
            return

        print(f"📂 Found {len(files)} documents.")

        # 4. Ingest
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

            n = await rag.ingest_document(content, metadata)
            print(f"     -> Added {n} chunks.")
            total_chunks += n

        print(f"\n{Fore.GREEN}✅ Ingestion Complete! Total Chunks: {total_chunks}")

    except Exception as e:
        print(f"{Fore.RED}❌ Error: {e}")
    finally:
        await rag.close()


if __name__ == "__main__":
    asyncio.run(main())
