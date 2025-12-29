import asyncio
import json
import os
import time

import httpx

# Configuration
BASE_URL = os.getenv("AGENT_URL", "http://localhost:8000")
ENDPOINT = f"{BASE_URL}/v1/chat/completions"

# Payload to trigger complex research skills
PAYLOAD = {
    "model": "gpt-4-turbo",  # Agent ignores model usually, but good for compat
    "messages": [
        {
            "role": "user",
            "content": (
                "Research Managed Identities and create a suggestion for a requirement "
                "on all teams to implement it in tibp."
            ),
        }
    ],
    "stream": True,
    "metadata": {"user_id": "debug_script", "debug": True},
}


async def run_diagnostic():
    print(f"🔹 Starting Diagnostic Stream to: {ENDPOINT}")
    print(f"🔹 Payload Prompt: {PAYLOAD['messages'][0]['content']}")
    print("-" * 60)

    start_time = time.perf_counter()
    last_chunk_time = start_time

    chunk_count = 0

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", ENDPOINT, json=PAYLOAD) as response:
                if response.status_code != 200:
                    print(f"❌ Error: Received status code {response.status_code}")
                    print(await response.aread())
                    return

                print(f"✅ Connection Established (Status: {response.status_code})")
                print(f"{'Time (s)':<10} | {'Delta (ms)':<10} | {'Type':<15} | {'Content Preview'}")
                print("-" * 60)

                async for line in response.aiter_lines():
                    now = time.perf_counter()
                    total_elapsed = now - start_time
                    delta_ms = (now - last_chunk_time) * 1000
                    last_chunk_time = now

                    if not line.startswith("data: "):
                        if line.strip():  # Log keepalives or errors
                            print(
                                f"{total_elapsed:<10.3f} | {delta_ms:<10.1f} | "
                                f"{'RAW':<15} | {line[:50]}"
                            )
                        continue

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        print(
                            f"{total_elapsed:<10.3f} | {delta_ms:<10.1f} | "
                            f"{'DONE':<15} | Stream Complete"
                        )
                        break

                    try:
                        chunk = json.loads(data_str)
                        # OpenWebUI Adapter format usually wraps info in 'choices'
                        # -> 'delta' -> 'content'
                        # Or it forwards 'agent' events directly if not strictly OpenAI compliant?
                        # Based on adapter code, it yields OpenAI-like chunks.

                        content = ""
                        chunk_type = "unknown"

                        if "choices" in chunk and len(chunk["choices"]) > 0:
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")

                            # Heuristic to detect thought vs content if encoded in content
                            if content.startswith("> 🧠"):
                                chunk_type = "thinking"
                            elif content.startswith("> 👣"):
                                chunk_type = "step"
                            else:
                                chunk_type = "content"

                        # Formatting for readability
                        preview = content.replace("\n", "\\n")[:50]
                        print(
                            f"{total_elapsed:<10.3f} | {delta_ms:<10.0f} | "
                            f"{chunk_type:<15} | {preview}"
                        )
                        chunk_count += 1

                    except json.JSONDecodeError:
                        print(
                            f"{total_elapsed:<10.3f} | {delta_ms:<10.1f} | "
                            f"{'ERROR':<15} | Failed to decode JSON: {data_str}"
                        )

    except httpx.ConnectError:
        print(f"❌ Connection Error: Could not connect to {BASE_URL}. Is the agent running?")
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")

    print("-" * 60)
    print(f"✅ Diagnostic Complete. Total Chunks: {chunk_count}")


if __name__ == "__main__":
    asyncio.run(run_diagnostic())
