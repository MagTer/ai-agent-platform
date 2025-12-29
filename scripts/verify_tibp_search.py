import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root / "services" / "agent" / "src"))

from core.tools.tibp_wiki_search import TibpWikiSearchTool  # noqa: E402


async def main():
    tool = TibpWikiSearchTool()
    print("Searching for 'UI Tests'...")
    result = await tool.run("UI Tests")
    print(f"Result:\n{result}")


if __name__ == "__main__":
    asyncio.run(main())
