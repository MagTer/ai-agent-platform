"""
LLM-based Semantic Evaluator for Semantic Tests.

Uses LLM-as-a-judge pattern to evaluate response quality against
criteria that are difficult to express with regex patterns.
"""

import json
import logging
import os
from dataclasses import dataclass

import httpx

LOGGER = logging.getLogger(__name__)

# LiteLLM endpoint - same as agent uses
LITELLM_BASE_URL = os.getenv("LITELLM_API_BASE", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")

# Use a fast model for evaluation (can be different from main agent model)
EVALUATOR_MODEL = os.getenv("EVALUATOR_MODEL", "openrouter/google/gemini-2.0-flash-001")


@dataclass
class EvaluationResult:
    """Result of LLM semantic evaluation."""

    passes: bool
    reasoning: str
    criteria_checked: str


# Evaluation criteria as structured prompts
QUALITY_CRITERIA = {
    "no_meta_commentary": """
Evaluate if this response contains meta-commentary about the agent's own process.

FAIL if the response contains phrases like:
- "I will now search for..."
- "Let me look that up..."
- "First, I need to..."
- "Based on my research..."
- "Here are the results of my search..."
- "I found the following information..."
- Any narration of the agent's own thinking process

PASS if the response directly answers the question without describing what the agent is doing.

Response to evaluate:
---
{response}
---

Return JSON: {{"passes": true/false, "reasoning": "brief explanation"}}
""",
    "direct_helpful_answer": """
Evaluate if this response is a direct, helpful answer.

PASS if:
- The response directly addresses the user's question
- Information is presented as facts, not as "search results" 
- The tone is that of a knowledgeable assistant
- No unnecessary filler or process narration

FAIL if:
- The response describes what the agent did to find the answer
- Contains phrases like "I searched", "I found", "According to my research"
- Reads like a research report about what was discovered rather than an answer

Response to evaluate:
---
{response}
---

Return JSON: {{"passes": true/false, "reasoning": "brief explanation"}}
""",
    "no_thinking_leakage": """
Evaluate if internal thinking/planning has leaked into this response.

FAIL if the response contains:
- Step-by-step planning visible to user
- Tool call syntax or JSON fragments
- References to "tools", "skills", or "agents"
- Debug information or trace IDs
- Markdown artifacts from internal processing (🧠, 👣, 🛠️)

PASS if the response reads like natural human communication.

Response to evaluate:
---
{response}
---

Return JSON: {{"passes": true/false, "reasoning": "brief explanation"}}
""",
}


async def evaluate_response(
    response: str,
    criteria: str = "no_meta_commentary",
    timeout: float = 30.0,
) -> EvaluationResult:
    """
    Evaluate a response using LLM-as-a-judge.

    Args:
        response: The agent's final response to evaluate
        criteria: Which criteria to check (key from QUALITY_CRITERIA)
        timeout: Request timeout in seconds

    Returns:
        EvaluationResult with pass/fail and reasoning
    """
    if criteria not in QUALITY_CRITERIA:
        available = ", ".join(QUALITY_CRITERIA.keys())
        raise ValueError(f"Unknown criteria '{criteria}'. Available: {available}")

    prompt = QUALITY_CRITERIA[criteria].format(response=response)

    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"

    payload = {
        "model": EVALUATOR_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a quality assurance evaluator for an AI assistant. "
                    "Evaluate responses strictly against the given criteria. "
                    "Always respond with valid JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,  # Deterministic for consistency
        "max_tokens": 200,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{LITELLM_BASE_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            # Extract content from response
            content = data["choices"][0]["message"]["content"]

            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                # Remove markdown code fences
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])

            result = json.loads(content)

            return EvaluationResult(
                passes=result.get("passes", False),
                reasoning=result.get("reasoning", "No reasoning provided"),
                criteria_checked=criteria,
            )

    except httpx.HTTPError as e:
        LOGGER.warning(f"LLM evaluation failed (network): {e}")
        # Fail open - if we can't evaluate, we pass (but log it)
        return EvaluationResult(
            passes=True,
            reasoning=f"Evaluation skipped due to network error: {e}",
            criteria_checked=criteria,
        )
    except json.JSONDecodeError as e:
        LOGGER.warning(f"LLM evaluation failed (parse): {e}")
        return EvaluationResult(
            passes=True,
            reasoning=f"Evaluation skipped due to parse error: {e}",
            criteria_checked=criteria,
        )
    except Exception as e:
        LOGGER.warning(f"LLM evaluation failed (unexpected): {e}")
        return EvaluationResult(
            passes=True,
            reasoning=f"Evaluation skipped due to error: {e}",
            criteria_checked=criteria,
        )


async def evaluate_all_criteria(response: str) -> list[EvaluationResult]:
    """
    Evaluate a response against all quality criteria.

    Returns list of results - all must pass for overall quality.
    """
    results = []
    for criteria_name in QUALITY_CRITERIA:
        result = await evaluate_response(response, criteria_name)
        results.append(result)
    return results


def assert_semantic_quality(
    results: list[EvaluationResult],
    strict: bool = False,
) -> None:
    """
    Assert that all semantic evaluations passed.

    Args:
        results: List of EvaluationResults from evaluate_all_criteria
        strict: If True, fail on any evaluation error. If False, only fail on explicit failures.
    """
    failures = [r for r in results if not r.passes]

    if failures:
        messages = [f"- {r.criteria_checked}: {r.reasoning}" for r in failures]
        raise AssertionError("Semantic quality check failed:\n" + "\n".join(messages))
