import json
import urllib.request
import urllib.error

def test_calculator():
    url = "http://localhost:8000/v1/chat/completions"
    data = {
        "model": "local/llama3-en",
        "messages": [
            {"role": "user", "content": "What is 12345 times 6789?"}
        ],
        "stream": False
    }
    
    print(f"Asking agent: '{data['messages'][0]['content']}'")
    
    try:
        req = urllib.request.Request(
            url, 
            data=json.dumps(data).encode('utf-8'),
            headers={"Content-Type": "application/json", "Authorization": "Bearer sk-dummy"}
        )
        with urllib.request.urlopen(req) as response:
            result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            print(f"Agent Response: {content}")
            
            # 12345 * 6789 = 83810205
            if "83810205" in content.replace(",", ""):
                 print("SUCCESS: Calculator produced correct result.")
            else:
                 print(f"WARNING: Result might be incorrect. Expected 83,810,205.")
            
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_calculator()
