import asyncio
from collections.abc import AsyncGenerator
from typing import Any


async def mock_executor_gen() -> AsyncGenerator[dict[str, Any], None]:
    print("Mock Executor: Starting")
    yield {"type": "thinking", "content": "Thinking 1"}
    await asyncio.sleep(0.01)
    yield {"type": "thinking", "content": "Thinking 2"}
    await asyncio.sleep(0.01)
    # Simulate CRASH: No 'result' yielded
    print("Mock Executor: Crashing (yielding nothing more)")
    # return/raise here simulates the silent exit or crash


async def service_logic():
    print("Service: Starting consumption")
    step_execution_result = None
    try:
        async for event in mock_executor_gen():
            print(f"Service received: {event['type']}")
            if event["type"] == "result":
                step_execution_result = event["result"]

        if not step_execution_result:
            print("Service: ERROR - ValueError('Executor failed to yield') would be raised here")
            raise ValueError("Executor failed to yield a result")

    except Exception as e:
        print(f"Service: Caught exception: {e}")


if __name__ == "__main__":
    asyncio.run(service_logic())
