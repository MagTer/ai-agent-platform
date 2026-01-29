"""
SSE Stream Parser for Semantic Tests.

Parses Server-Sent Events from the agent and separates thinking blocks
from the final answer.
"""

import json
import re
from dataclasses import dataclass, field

# Patterns that indicate "thinking" or internal monologue
THINKING_PREFIXES = (
    "🧠",  # Brain - thinking/reasoning
    "👣",  # Footprints - steps/plan
    "🛠️",  # Tools
    "✅",  # Success
    "❌",  # Error
    "🔍",  # Search
    "🌐",  # Web fetch
    "📝",  # Composing
    "⚙️",  # Activity
    "📄",  # File
)


@dataclass
class ParsedResponse:
    """Parsed SSE response separating thinking from final answer."""

    thinking_blocks: list[str] = field(default_factory=list)
    final_answer: str = ""
    raw_chunks: list[dict] = field(default_factory=list)
    error_chunks: list[str] = field(default_factory=list)
    trace_id: str = ""


def parse_sse_stream(raw_lines: list[str], trace_id: str = "") -> ParsedResponse:
    """
    Parse SSE lines into structured response.

    Args:
        raw_lines: List of raw SSE lines from the response
        trace_id: Optional trace ID from response headers

    Returns:
        ParsedResponse with thinking blocks and final answer separated
    """
    result = ParsedResponse(trace_id=trace_id)
    content_parts: list[str] = []
    thinking_parts: list[str] = []

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        if line == "data: [DONE]":
            break
        if not line.startswith("data: "):
            continue

        json_str = line[6:]  # Remove "data: " prefix
        try:
            chunk = json.loads(json_str)
            result.raw_chunks.append(chunk)

            # Extract content from delta
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")

                if content:
                    # Check if this is a thinking block
                    stripped = content.lstrip()
                    is_thinking = any(stripped.startswith(p) for p in THINKING_PREFIXES)

                    if is_thinking:
                        thinking_parts.append(content)
                    else:
                        # Check for inline thinking patterns
                        if _contains_thinking_pattern(content):
                            thinking_parts.append(content)
                        else:
                            content_parts.append(content)

        except json.JSONDecodeError:
            # Skip malformed JSON
            continue

    # Join parts
    result.thinking_blocks = _split_thinking_blocks(thinking_parts)
    result.final_answer = "".join(content_parts).strip()

    return result


def _contains_thinking_pattern(content: str) -> bool:
    """Check if content contains inline thinking patterns."""
    thinking_patterns = [
        r"\*\*Plan:\*\*",
        r"\*\*Executor:\*\*",
        r"\*\*Agent:\*\*",
        r"Generating plan\.\.\.",
        r"\[TraceID:",
    ]
    for pattern in thinking_patterns:
        if re.search(pattern, content):
            return True
    return False


def _split_thinking_blocks(parts: list[str]) -> list[str]:
    """Split thinking content into logical blocks."""
    if not parts:
        return []

    combined = "".join(parts)
    # Split on double newlines to get distinct blocks
    blocks = [b.strip() for b in combined.split("\n\n") if b.strip()]
    return blocks


def extract_final_answer_only(raw_lines: list[str]) -> str:
    """
    Convenience function to get just the final answer.

    Use this when you only care about what the user sees.
    """
    parsed = parse_sse_stream(raw_lines)
    return parsed.final_answer
