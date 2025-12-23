import json
import urllib.error
import urllib.request


def test_clock():
    url = "http://localhost:8000/v1/chat/completions"
    data = {
        "model": "local/llama3-en",
        "messages": [{"role": "user", "content": "What time is it right now? Use the clock tool."}],
        "stream": False,
    }

    print(f"Asking agent: '{data['messages'][0]['content']}'")

    try:
        req = urllib.request.Request(  # noqa: S310
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer sk-dummy"},
        )
        with urllib.request.urlopen(req) as response:  # noqa: S310
            result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            print(f"Agent Response: {content}")

            # Check if tool was used (metadata might show steps if we exposed them in response)
            # But the content should be a time.
            if "202" in content or ":" in content:
                print("SUCCESS: Agent returned a relevant time/date string.")
            else:
                print("WARNING: Response might not be time-related.")

    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    test_clock()
