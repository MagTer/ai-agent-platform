import logging

from core.tools.base import Tool
from modules.context7.client import get_context7_client

LOGGER = logging.getLogger(__name__)


class Context7SearchTool(Tool):
    name = "context7_search_libraries"
    description = (
        "Search for a library ID compatible with Context7 using a query string. "
        "Returns a list of matching libraries with their IDs, descriptions, and benchmarks. "
        "Use this BEFORE fetching documentation to get the correct 'library_id'."
    )

    async def run(self, query: str) -> str:
        client = await get_context7_client()
        try:
            # Map our clean arg 'query' to the underlying tool arg 'libraryName'
            # The Context7 tool is named 'resolve-library-id'
            result = await client.call_tool("resolve-library-id", {"libraryName": query})

            # Result content is usually a list of TextContent objects
            content_list = result.content
            output = ""
            for item in content_list:
                if item.type == "text":
                    output += item.text
            return output
        except Exception as e:
            LOGGER.exception("Context7 search failed")
            return f"Error searching libraries: {str(e)}"


class Context7GetDocsTool(Tool):
    name = "context7_get_docs"
    description = (
        "Fetch documentation for a specific library using its Context7 ID. "
        "Requires a valid 'library_id' (e.g., '/vercel/next.js') from 'context7_search_libraries'. "
        "Modes: 'code' (API refs) or 'info' (Guides)."
    )

    async def run(
        self,
        library_id: str,
        mode: str = "code",
        topic: str | None = None,
        page: int = 1,
    ) -> str:
        client = await get_context7_client()
        try:
            # underlying tool: 'get-library-docs'
            # args: context7CompatibleLibraryID, mode, topic, page
            args = {
                "context7CompatibleLibraryID": library_id,
                "mode": mode,
                "page": page,
            }
            if topic:
                args["topic"] = topic

            result = await client.call_tool("get-library-docs", args)

            content_list = result.content
            output = ""
            for item in content_list:
                if item.type == "text":
                    output += item.text
            return output

        except Exception as e:
            LOGGER.exception("Context7 docs fetch failed")
            return f"Error fetching docs: {str(e)}"
