import time
from playwright.sync_api import sync_playwright

print("Navigating to http://127.0.0.1:9090/ via Playwright...")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    errors = []
    logs = []
    
    page.on("pageerror", lambda err: errors.append(f"PAGEERROR: {err}"))
    page.on("console", lambda msg: logs.append(f"CONSOLE [{msg.type}]: {msg.text}"))
    
    page.goto("http://127.0.0.1:9090/", wait_until="networkidle")
    page.wait_for_timeout(2000)
    
    print("\n" + "="*50)
    print("=== PAGE ERRORS (SHOULD BE ZERO) ===")
    print("="*50)
    for err in errors:
        print(err)
    if not errors:
        print(">> ZERO ERRORS! SUCCESS! <<")
        
    print("\n" + "="*50)
    print("=== TESTING BUTTON INTERACTIONS ===")
    print("="*50)
    
    # Test clicking tabs
    tabs = page.query_selector_all(".sidebar-item")
    print(f"Found {len(tabs)} sidebar items.")
    for i, tab in enumerate(tabs):
        text = tab.inner_text().strip().replace('\n', ' ')
        print(f"  Tab {i}: {text}")
        tab.click()
        page.wait_for_timeout(300)
        
    # Test typing in chat input
    chat_input = page.query_selector("#chat-input")
    if chat_input:
        chat_input.fill("테스트 메시지입니다.")
        val = chat_input.input_value()
        print(f">> Chat input typed successfully: '{val}'")
    else:
        print(">> FAILED: chat-input not found")
        
    # Check send button
    send_btn = page.query_selector("#send-button")
    if send_btn:
        print(f">> Send button found! Enabled: {send_btn.is_enabled()}")
    else:
        print(">> FAILED: send-button not found")
        
    # Check model selector
    model_btn = page.query_selector("#provider-drawer-container") or page.query_selector(".model-select-btn")
    print(f">> Model selector element found: {bool(model_btn)}")
    
    browser.close()
    print("\nAll interactive verification finished!")
