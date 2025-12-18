import asyncio

from core.tools.web_search import WebSearchTool


async def main():
    print("Initializing WebSearchTool...")
    tool = WebSearchTool(base_url="http://webfetch:8081", max_results=3)

    print("Running search for 'Llama 3'...")
    try:
        result = await tool.run("Llama 3")
        print("\n--- RESULT ---")
        print(result)
        print("--------------\n")
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(main())
