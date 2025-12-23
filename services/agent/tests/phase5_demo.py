import asyncio
import httpx
import json

AGENT_URL = "http://localhost:8000/v1/agent"

async def main():
    print("### Phase 5 Verification Demo ###")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Trigger Indexing
        print("\n[1] Triggering Codebase Indexing...")
        # We need to craft a request that triggers the tool.
        # "Index the codebase now."
        req_index = {
            "prompt": "Index the codebase now. Use the tool.",
            "metadata": {"routing_decision": "AGENTIC"}
        }
        try:
            resp = await client.post(AGENT_URL, json=req_index)
            resp.raise_for_status()
            data = resp.json()
            print(f"Response: {data['response']}")
            # Check steps
            if "steps" in data["metadata"]:
                for step in data["metadata"]["steps"]:
                    if step.get("type") == "tool" and step.get("name") == "index_codebase":
                        print(">> Tool 'index_codebase' was executed successfully.")
        except Exception as e:
            print(f"Indexing Failed: {e}")

        # 2. Search Code
        print("\n[2] Searching Codebase...")
        req_search = {
            "prompt": "Search for the Context model definition in models.py. What fields does it have?",
            "metadata": {"routing_decision": "AGENTIC"}
        }
        try:
            resp = await client.post(AGENT_URL, json=req_search)
            resp.raise_for_status()
            data = resp.json()
            print(f"Response: {data['response']}")
             # Check steps
            if "steps" in data["metadata"]:
                for step in data["metadata"]["steps"]:
                    if step.get("type") == "tool" and step.get("name") == "search_codebase":
                        print(">> Tool 'search_codebase' was executed.")
        except Exception as e:
            print(f"Search Failed: {e}")

        # 3. Pin File
        print("\n[3] Pinning a File...")
        # We need a relative path that works. 
        # The agent runs in /app. 
        # Source code is at /app/src.
        # Let's pin "src/core/db/models.py".
        target_file = "/app/src/core/db/models.py"
        req_pin = {
            "prompt": f"Pin the file {target_file} to context.",
            "metadata": {"routing_decision": "AGENTIC"}
        }
        try:
            resp = await client.post(AGENT_URL, json=req_pin)
            resp.raise_for_status()
            data = resp.json()
            print(f"Response: {data['response']}")
            
             # Check steps
            if "steps" in data["metadata"]:
                for step in data["metadata"]["steps"]:
                    if step.get("type") == "tool" and step.get("name") == "pin_file":
                        print(">> Tool 'pin_file' was executed.")
        except Exception as e:
            print(f"Pinning Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
