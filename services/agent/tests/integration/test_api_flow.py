import json
import pytest
import httpx
import os

# Define base URL for the agent service. 
# In CI/Docker this would be http://agent:8000
# For local testing, we assume localhost:8000 or use env var.
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8000")

@pytest.mark.asyncio
async def test_chat_completions_stream_flow():
    """
    Test the v1/chat/completions endpoint with streaming enabled.
    This simulates how OpenWebUI interacts with the agent.
    """
    url = f"{AGENT_BASE_URL}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-dummy"
    }
    payload = {
        "model": "local/llama3-en",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "stream": True,
        "metadata": {
            "platform": "integration_test",
            "platform_id": "test_runner"
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                assert response.status_code == 200, f"Request failed with status {response.status_code}"
                
                received_chunks = []
                async for chunk in response.aiter_lines():
                    if not chunk.strip():
                        continue
                    if chunk.startswith("data: [DONE]"):
                        break
                    if chunk.startswith("data: "):
                        data_str = chunk[6:]
                        try:
                            data = json.loads(data_str)
                            received_chunks.append(data)
                        except json.JSONDecodeError:
                            print(f"Failed to decode json: {data_str}")
                
                assert len(received_chunks) > 0, "No data chunks received"
                
                # Check structure of at least one chunk
                first_chunk = received_chunks[0]
                assert "id" in first_chunk
                assert "choices" in first_chunk
                assert "delta" in first_chunk["choices"][0]

        except httpx.ConnectError:
            pytest.skip("Agent service not running on localhost:8000. Start docker-compose to run this test.")

@pytest.mark.asyncio
async def test_chat_completions_basic_flow():
    """
    Test non-streaming request (if supported, though adapter focuses on stream).
    This validates the route_message logic returns a valid Plan or Response.
    """
    url = f"{AGENT_BASE_URL}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-dummy"
    }
    # Using a known tool command to likely trigger a plan
    payload = {
        "model": "local/llama3-en",
        "messages": [
            {"role": "user", "content": "/help"} 
        ],
        "stream": False,
         "metadata": {
            "platform": "integration_test",
            "platform_id": "test_runner_slash"
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            # If the adapter forces streaming for now, we might get 200 with stream or error.
            # The current adapter implementation handles stream=False by mocking response.
            assert response.status_code == 200
            data = response.json()
            assert data["object"] == "chat.completion"
            assert len(data["choices"]) > 0
            assert "content" in data["choices"][0]["message"]
            
        except httpx.ConnectError:
            pytest.skip("Agent service not running. Start docker-compose.")
