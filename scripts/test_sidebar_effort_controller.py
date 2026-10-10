from playwright.sync_api import sync_playwright
import time
import json
import urllib.request

print("Starting sidebar effort controller test...", flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    
    page.goto('http://127.0.0.1:9090')
    page.wait_for_load_state('domcontentloaded')
    time.sleep(2)

    # 1. Check sidebar effort buttons
    btns = page.query_selector_all('.sidebar-effort-btn')
    print(f"Found {len(btns)} sidebar effort buttons", flush=True)

    badge = page.query_selector('#sidebar-effort-label')
    if badge:
        print("Initial sidebar effort badge:", badge.text_content().strip(), flush=True)

    # Take screenshot of sidebar
    page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/sidebar_effort_initial.png')

    # 2. Click 'low' button
    low_btn = page.query_selector('.sidebar-effort-btn[data-effort="low"]')
    if low_btn:
        print("Clicking 'low' effort button in sidebar...", flush=True)
        low_btn.click()
        time.sleep(1)
        if badge:
            print("Badge after clicking low:", badge.text_content().strip(), flush=True)
        page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/sidebar_effort_low.png')

    # 3. Click 'high' button
    high_btn = page.query_selector('.sidebar-effort-btn[data-effort="high"]')
    if high_btn:
        print("Clicking 'high' effort button in sidebar...", flush=True)
        high_btn.click()
        time.sleep(1)
        if badge:
            print("Badge after clicking high:", badge.text_content().strip(), flush=True)
        page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/sidebar_effort_high.png')

    browser.close()

# 4. Verify API persistence
try:
    req = urllib.request.urlopen('http://127.0.0.1:9090/api/providers')
    data = json.loads(req.read().decode('utf-8'))
    opencode_go = data['providers'].get('opencode-go', {})
    models = opencode_go.get('models', [])
    for m in models:
        if m.get('id') == 'deepseek-v4.1-flash':
            print(f"Verified via API: deepseek-v4.1-flash reasoning_effort is '{m.get('reasoning_effort')}'", flush=True)
except Exception as e:
    print("API check error:", e, flush=True)

print("Test complete!", flush=True)
