import json
import urllib.request
import urllib.error
import sys

def test_connection():
    url = "http://localhost:8000/v1/chat/completions"
    data = {
        "model": "local/llama3-en",
        "messages": [
            {"role": "user", "content": "Hello, are you online?"}
        ],
        "stream": False
    }
    
    print(f"Testing connection to {url}...")
    
    try:
        req = urllib.request.Request(
            url, 
            data=json.dumps(data).encode('utf-8'),
            headers={"Content-Type": "application/json", "Authorization": "Bearer sk-dummy"}
        )
        with urllib.request.urlopen(req) as response:
            result = json.load(response)
            print("Response:", json.dumps(result, indent=2))
            print("SUCCESS: Connected to Agent API.")
            
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        print(f"URL Error: {e.reason}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_connection()
