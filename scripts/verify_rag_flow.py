import argparse
import logging
import sys
import time
import uuid
from typing import Any

import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
LOGGER = logging.getLogger(__name__)

# Constants
DEFAULT_RAGPROXY_URL = "http://localhost:8083"
DEFAULT_WEBFETCH_URL = "http://localhost:8081"
DEFAULT_EMBEDDER_URL = "http://localhost:8082"
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_COLLECTION = "memory"

TEST_URL = "https://example.com/rag-test-dummy"
TEST_CONTENT = "The secret code for the platform verification is ALPHA-TANGO-99."


def ingest_test_data(
    webfetch_url: str, embedder_url: str, qdrant_url: str, collection: str
) -> None:
    """Simulate ingestion of a test document directly into Qdrant."""
    LOGGER.info(f"Ingesting test data for {TEST_URL}...")

    # 1. Ensure collection exists
    try:
        # Check existence
        r = requests.get(f"{qdrant_url.rstrip('/')}/collections/{collection}", timeout=10)
        if r.status_code != 200:
            LOGGER.info(f"Collection '{collection}' not found. Creating...")
            # Create collection
            r = requests.put(
                f"{qdrant_url.rstrip('/')}/collections/{collection}",
                json={"vectors": {"size": 384, "distance": "Cosine"}},
                timeout=30,
            )
            r.raise_for_status()
            LOGGER.info(f"Collection '{collection}' created.")
    except Exception as e:
        LOGGER.error(f"Failed to ensure collection exists: {e}")
        sys.exit(1)

    # 2. Embed the text
    try:
        r = requests.post(
            f"{embedder_url.rstrip('/')}/embed",
            json={"inputs": [TEST_CONTENT], "normalize": True},
            timeout=30,
        )
        r.raise_for_status()
        vector = r.json().get("vectors", [])[0]
    except Exception as e:
        LOGGER.error(f"Failed to embed test content: {e}")
        sys.exit(1)

    # 2. Upsert to Qdrant
    try:
        # Using the same ID generation logic as ingest.py for consistency,
        # though strictly not required for this test if we just want *some* data.
        pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{TEST_URL}#0"))

        payload = {
            "url": TEST_URL,
            "text": TEST_CONTENT,
            "chunk_ix": 0,
            "ts": int(time.time()),
            "source": "web",
        }

        # We need to construct the Qdrant point.
        # Since we don't want to depend on the 'qdrant_client' library if not strictly needed
        # (to keep this script lightweight), we use the REST API directly.

        point = {"id": pid, "vector": vector, "payload": payload}

        r = requests.put(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points",
            params={"wait": "true"},
            json={"points": [point]},
            timeout=30,
        )
        r.raise_for_status()
        LOGGER.info("Test data upserted successfully.")

        # 3. VERIFY DATA EXISTS (Debug Step)
        r = requests.get(f"{qdrant_url.rstrip('/')}/collections/{collection}", timeout=10)
        LOGGER.info(f"Collection Info: {r.text}")

        # 4. VERIFY RETRIEVAL (Debug Step)
        # Search using the same vector we just upserted
        search_payload = {"vector": vector, "limit": 1, "with_payload": True, "with_vector": True}
        r = requests.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/search",
            json=search_payload,
            timeout=10,
        )
        LOGGER.info(f"Direct Search Result: {r.text}")

    except Exception as e:
        LOGGER.error(f"Failed to upsert or verify Qdrant: {e}")
        sys.exit(1)


def query_rag_proxy(ragproxy_url: str) -> dict[str, Any]:
    """Query the RAG proxy and return the response."""
    LOGGER.info("Querying RAG proxy...")

    payload = {
        "model": "rag/llama3-en",
        "messages": [
            {"role": "user", "content": "What is the secret code for platform verification?"}
        ],
    }

    try:
        r = requests.post(
            f"{ragproxy_url.rstrip('/')}/v1/chat/completions", json=payload, timeout=60
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        LOGGER.error(f"Failed to query RAG proxy: {e}")
        sys.exit(1)


def verify_response(response: dict[str, Any]) -> None:
    """Verify that the response contains the correct answer and citations."""
    try:
        content = response["choices"][0]["message"]["content"]
        LOGGER.info(f"LLM Response:\n{content}")

        # Check for citation marker
        if "[1]" not in content:
            LOGGER.error("FAIL: Response does not contain citation marker [1]")
            sys.exit(1)

        # Check for correct detailed info
        if "ALPHA-TANGO-99" not in content:
            LOGGER.error("FAIL: Response does not contain the expected secret code")
            sys.exit(1)

        # Check for Sources section (simple check)
        if "Sources" not in content and "Source" not in content:
            LOGGER.error("FAIL: Response does not appear to list sources")
            sys.exit(1)

        LOGGER.info("SUCCESS: RAG verification passed!")

    except (KeyError, IndexError) as e:
        LOGGER.error(f"Failed to parse LLM response: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Verify RAG End-to-End Flow")
    parser.add_argument("--ragproxy", default=DEFAULT_RAGPROXY_URL, help="RAG Proxy URL")
    parser.add_argument("--embedder", default=DEFAULT_EMBEDDER_URL, help="Embedder URL")
    parser.add_argument("--qdrant", default=DEFAULT_QDRANT_URL, help="Qdrant URL")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Qdrant collection")

    args = parser.parse_args()

    ingest_test_data(args.webfetch, args.embedder, args.qdrant, args.collection) if hasattr(
        args, "webfetch"
    ) else ingest_test_data("ignored", args.embedder, args.qdrant, args.collection)
    response = query_rag_proxy(args.ragproxy)
    verify_response(response)


if __name__ == "__main__":
    main()
