"""Content classification for LLM streaming output.

Centralizes detection and filtering of raw model tokens, noise fragments,
and reasoning content. Used by the LiteLLM client (source-level filtering)
and adapters (defense-in-depth).
"""

from __future__ import annotations

import enum
import re

# Raw model tokens that should never be shown to users.
# These indicate internal chain-of-thought or model formatting
# leaked during ZDR failover between providers (Groq/DeepInfra/Novita).
RAW_MODEL_TOKENS = frozenset(
    [
        "<|header_start|>",
        "<|header_end|>",
        "<|im_start|>",
        "<|im_end|>",
        "<|endoftext|>",
        "<|assistant|>",
        "<|user|>",
        "<|system|>",
        "<|ipython|>",
        "<think>",
        "</think>",
        "<|eot_id|>",
        "<|start_header_id|>",
        "<|end_header_id|>",
    ]
)

# Compiled regex for stripping tokens from content
_RAW_TOKEN_PATTERN = re.compile("|".join(re.escape(t) for t in RAW_MODEL_TOKENS))

# Patterns that indicate model reasoning/planning output (not final content).
# Used for more aggressive filtering in DEFAULT mode.
REASONING_PATTERNS = [
    # Tool call patterns from reasoning models
    re.compile(r"^\s*\[?\s*web_search\s*\(", re.IGNORECASE),
    re.compile(r"^\s*\[?\s*web_fetch\s*\(", re.IGNORECASE),
    re.compile(r"^\s*\[?\s*\w+_\w+\s*\(.*\)\s*[,\]]", re.IGNORECASE),  # tool_name(...)
    # Planning/reasoning phrases at start of content
    re.compile(r"^(Let's|Let me|I'll|I will|I need to|First,|Step \d)", re.IGNORECASE),
    re.compile(r"^(Now I|Now let|Proceeding|Starting|Begin)", re.IGNORECASE),
    re.compile(r"^(Turn \d|Round \d|Attempt \d)", re.IGNORECASE),
    re.compile(r"^(Next,|Then,|After that|Finally,|Additionally)", re.IGNORECASE),
    # Partial reasoning phrases (may appear mid-stream)
    re.compile(r"start by searching", re.IGNORECASE),
    re.compile(r"let's fetch", re.IGNORECASE),
    re.compile(r"fetch (some|the|another|more)", re.IGNORECASE),
    re.compile(r"searching for (the|Tesla|more)", re.IGNORECASE),
    re.compile(r"get (the latest|more info)", re.IGNORECASE),
    # JSON-like tool calls
    re.compile(r'^\s*\{\s*"tool"\s*:', re.IGNORECASE),
    re.compile(r'^\s*\{\s*"action"\s*:', re.IGNORECASE),
]


class ContentCategory(enum.Enum):
    """Classification result for streaming content chunks."""

    RAW_TOKEN = "raw_token"  # noqa: S105
    NOISE = "noise"
    REASONING = "reasoning"
    CLEAN = "clean"


def contains_raw_model_tokens(content: str) -> bool:
    """Check if content contains raw model tokens that should be filtered.

    These tokens indicate internal chain-of-thought or model formatting
    that shouldn't be displayed to end users.
    """
    if not content:
        return False
    for token in RAW_MODEL_TOKENS:
        if token in content:
            return True
    return False


def strip_raw_tokens(content: str) -> str:
    """Remove raw model tokens from content, returning the cleaned string.

    Returns an empty string if the content consists entirely of tokens.
    """
    if not content:
        return content
    return _RAW_TOKEN_PATTERN.sub("", content)


def is_reasoning_content(content: str) -> bool:
    """Check if content looks like model reasoning/planning output.

    This catches natural language reasoning that reasoning models output
    as content instead of in reasoning_content field.

    NOTE: This function is NOT used for streaming content filtering because
    streaming sends partial chunks that may match patterns incorrectly.
    It's kept for potential use in buffered content filtering.
    """
    if not content:
        return False
    for pattern in REASONING_PATTERNS:
        if pattern.search(content):
            return True
    return False


def is_noise_fragment(content: str) -> bool:
    """Check if content is a noise fragment from reasoning model streaming.

    Reasoning models often stream their chain-of-thought character by character,
    resulting in fragments like '[', '[]', '{', etc. that should be filtered.
    """
    if not content:
        return False

    stripped = content.strip()

    # Very short fragments that are likely noise
    if len(stripped) <= 3:
        # Single brackets, braces, or short combos
        if stripped in ("[", "]", "[]", "{", "}", "{}", "(", ")", "()", "[{", "}]"):
            return True
        # Single punctuation or whitespace
        if stripped in (",", ":", ";", ".", "\n", "\t"):
            return True

    return False


def classify_content(content: str) -> ContentCategory:
    """Classify content into a category. Priority: RAW_TOKEN > NOISE > REASONING > CLEAN."""
    if contains_raw_model_tokens(content):
        return ContentCategory.RAW_TOKEN
    if is_noise_fragment(content):
        return ContentCategory.NOISE
    if is_reasoning_content(content):
        return ContentCategory.REASONING
    return ContentCategory.CLEAN
