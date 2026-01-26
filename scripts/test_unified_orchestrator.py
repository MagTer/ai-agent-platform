#!/usr/bin/env python3
"""Test script to evaluate if gpt-oss-120b can handle unified orchestration (Option C).

Tests whether the model can:
1. Answer simple questions directly (no plan needed)
2. Return a plan for complex requests requiring skills

Measures response time for each case.
"""

import asyncio
import json
import time
from typing import Any

import httpx

LITELLM_URL = "http://localhost:4001/v1/chat/completions"
MODEL = "planner"  # Maps to gpt-oss-120b in litellm config

SYSTEM_PROMPT = """You are a smart orchestrator. Based on the user's request, do ONE of:

1. **DIRECT ANSWER**: If you can answer immediately (translations, math,
   general knowledge, greetings), respond with plain text.

2. **PLAN**: If the request needs external tools/skills (web search, smart home,
   Azure DevOps, current data), return JSON:
```json
{"plan": [{"skill": "skill_name", "goal": "what to accomplish"}]}
```

Available skills:
- researcher: Web research with page reading
- deep_research: Comprehensive multi-source research
- search: Quick web search
- general/homey: Smart home control
- backlog_manager: Azure DevOps queries

IMPORTANT:
- Simple questions = direct text answer
- Needs current data or actions = JSON plan
- Never wrap direct answers in JSON"""

TEST_CASES = [
    # Simple questions - should answer directly
    ("Vad är hej på franska?", "direct"),
    ("What is 2 + 2?", "direct"),
    ("Translate 'good morning' to Swedish", "direct"),
    ("What's the capital of France?", "direct"),
    ("Explain what Python is in one sentence", "direct"),
    # Complex requests - should return plan
    ("Sök efter senaste nyheterna om AI", "plan"),
    ("Släck lampan i köket", "plan"),
    ("Do a deep research on GLP-1 medications", "plan"),
    ("What are the latest Python 3.13 features?", "plan"),  # Needs current data
    ("Lista mina work items i Azure DevOps", "plan"),
]


async def test_prompt(client: httpx.AsyncClient, prompt: str) -> tuple[str, float, Any]:
    """Send prompt and measure response time."""
    start = time.perf_counter()

    response = await client.post(
        LITELLM_URL,
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        },
        timeout=30.0,
    )

    elapsed = time.perf_counter() - start

    if response.status_code != 200:
        return f"ERROR: {response.status_code}", elapsed, None

    data = response.json()
    content = data["choices"][0]["message"]["content"]

    return content, elapsed, data


def classify_response(content: str) -> str:
    """Determine if response is direct answer or plan."""
    content_stripped = content.strip()

    # Check if it's JSON (plan)
    if content_stripped.startswith("{") or content_stripped.startswith("```json"):
        # Try to parse as JSON
        try:
            # Remove markdown code fences if present
            json_str = content_stripped
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]

            parsed = json.loads(json_str.strip())
            if "plan" in parsed or "steps" in parsed:
                return "plan"
        except json.JSONDecodeError:
            pass

    return "direct"


async def main():
    print("=" * 70)
    print("Testing Unified Orchestrator (Option C) with gpt-oss-120b")
    print("=" * 70)
    print()

    async with httpx.AsyncClient() as client:
        results = []

        for prompt, expected in TEST_CASES:
            print(f"Testing: {prompt[:50]}...")

            try:
                content, elapsed, _ = await test_prompt(client, prompt)
                actual = classify_response(content)

                match = "✓" if actual == expected else "✗"
                results.append(
                    {
                        "prompt": prompt,
                        "expected": expected,
                        "actual": actual,
                        "match": actual == expected,
                        "time_ms": elapsed * 1000,
                        "response": content[:200],
                    }
                )

                print(f"  {match} Expected: {expected}, Got: {actual}, Time: {elapsed*1000:.0f}ms")
                if actual != expected:
                    print(f"    Response: {content[:100]}...")
                print()

            except Exception as e:
                print(f"  ✗ Error: {e}")
                results.append(
                    {
                        "prompt": prompt,
                        "expected": expected,
                        "actual": "error",
                        "match": False,
                        "time_ms": 0,
                        "response": str(e),
                    }
                )

        # Summary
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)

        correct = sum(1 for r in results if r["match"])
        total = len(results)

        direct_times = [r["time_ms"] for r in results if r["expected"] == "direct" and r["match"]]
        plan_times = [r["time_ms"] for r in results if r["expected"] == "plan" and r["match"]]

        print(f"Accuracy: {correct}/{total} ({100*correct/total:.0f}%)")
        print()

        if direct_times:
            print("Direct answer times:")
            print(f"  Min: {min(direct_times):.0f}ms")
            print(f"  Max: {max(direct_times):.0f}ms")
            print(f"  Avg: {sum(direct_times)/len(direct_times):.0f}ms")

        print()

        if plan_times:
            print("Plan generation times:")
            print(f"  Min: {min(plan_times):.0f}ms")
            print(f"  Max: {max(plan_times):.0f}ms")
            print(f"  Avg: {sum(plan_times)/len(plan_times):.0f}ms")

        print()
        print("=" * 70)

        # Show failures
        failures = [r for r in results if not r["match"]]
        if failures:
            print("FAILURES:")
            for f in failures:
                print(f"  - {f['prompt'][:40]}...")
                print(f"    Expected: {f['expected']}, Got: {f['actual']}")
                print(f"    Response: {f['response'][:150]}...")
                print()


if __name__ == "__main__":
    asyncio.run(main())
