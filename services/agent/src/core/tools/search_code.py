from modules.rag import RAGManager
from core.tools.base import Tool, ToolError


class SearchCodeBaseTool(Tool):
    """
    Semantic code search using RAG.
    """

    name = "search_codebase"
    description = (
        "Semantically search the codebase for relevant snippets. "
        "Useful for finding definitions, usage examples, and understanding code without knowing exact paths. "
        "Args: query (str)"
    )

    def __init__(self) -> None:
        self.rag = RAGManager()

    async def run(self, query: str) -> str:
        try:
            results = await self.rag.retrieve(query, filters={"source": "codebase"})
            if not results:
                return "No relevant code snippets found."

            output = [f"Found {len(results)} relevant snippets for '{query}':\n"]
            for i, doc in enumerate(results, 1):
                filepath = doc.get("filepath", "unknown")
                score = doc.get("score", 0.0)
                name = doc.get("name", "")
                snippet_type = doc.get("type", "")
                text = doc.get("text", "").strip()

                header = f"{i}. [{filepath}]"
                if name:
                    header += f" ({snippet_type}: {name})"
                header += f" (Score: {score:.2f})"
                
                output.append(header)
                output.append("```python")
                output.append(text)
                output.append("```\n")

            return "\n".join(output)
        except Exception as e:
            raise ToolError(f"Search failed: {e}") from e
