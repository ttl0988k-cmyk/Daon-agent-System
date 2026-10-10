# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto('http://127.0.0.1:9090', wait_until='domcontentloaded')
    page.wait_for_timeout(2000)
    
    # 1. Check sidebar sessions
    sessions = page.locator('#recent-threads-list a').all_inner_texts()
    print("Sidebar sessions rendered:", sessions[:5])
    
    # 2. Check provider drawer toggle
    prov_toggle = page.locator('#provider-drawer-toggle')
    prov_toggle.click()
    page.wait_for_timeout(500)
    provider_pills = page.locator('#provider-pills-list button').all_inner_texts()
    print("Provider pills in UI:", provider_pills)
    
    # 3. Check workspace button in header
    ws_btn = page.locator('#header-workspace-btn')
    ws_btn.click()
    page.wait_for_timeout(500)
    ws_modal = page.locator('#workspace-modal')
    print("Workspace modal is visible:", ws_modal.is_visible())
    
    screenshot_path = r"C:\Users\ttl09\.gemini\antigravity-ide\brain\062de52b-d58f-4fd9-b45a-55e1941021f0\verified_providers_and_sessions.png"
    page.screenshot(path=screenshot_path)
    print("Screenshot saved to", screenshot_path)
    
    browser.close()
