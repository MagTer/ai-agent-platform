import asyncio
import os
import sys

# Ensure imports work
sys.path.append("/app/src")

from core.tools.web_search import WebSearchTool

async def main():
    print("Initializing WebSearchTool...")
    # Instantiate with dummy base_url as it's ignored
    tool = WebSearchTool(base_url="http://searxng:8080")
    
    query = "current date and time"
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    
    print(f"Running search for: '{query}'")
    try:
        result = await tool.run(query)
        print("\n=== SEARCH RESULTS ===")
        print(result)
        print("======================")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
