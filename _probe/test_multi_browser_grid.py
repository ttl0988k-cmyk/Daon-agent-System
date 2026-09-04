import urllib.request
import json
import sys

def test_grid_api():
    base_url = "http://127.0.0.1:9090"
    
    print("--- 1. Testing GET /api/browser/grid ---")
    try:
        req = urllib.request.Request(f"{base_url}/api/browser/grid")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print("Status Code:", resp.status)
            print("Response:", json.dumps(data, indent=2, ensure_ascii=False)[:500])
            print("Grid OK: tabs count =", len(data.get("tabs", [])))
    except Exception as e:
        print("Grid test error:", e)

    print("\n--- 2. Testing POST /api/browser/navigate with session_id ---")
    try:
        nav_body = json.dumps({
            "url": "https://www.example.com",
            "session_id": "session_test_A"
        }).encode('utf-8')
        req = urllib.request.Request(f"{base_url}/api/browser/navigate", data=nav_body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print("Navigate Status:", resp.status)
            print("Navigate Response:", data)
    except Exception as e:
        print("Navigate test error:", e)

    print("\n--- 3. Testing GET /api/browser/grid after navigate ---")
    try:
        req = urllib.request.Request(f"{base_url}/api/browser/grid")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            tabs = data.get("tabs", [])
            print("Tabs count:", len(tabs))
            for t in tabs:
                print(f"Tab ID: {t.get('id')}, Session: {t.get('session_id')}, Title: {t.get('title')}, URL: {t.get('url')}, ThumbLen: {len(t.get('thumbnail', ''))}")
    except Exception as e:
        print("Grid post-nav error:", e)

if __name__ == "__main__":
    test_grid_api()
