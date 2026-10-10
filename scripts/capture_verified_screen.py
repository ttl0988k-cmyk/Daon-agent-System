# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto('http://127.0.0.1:9090', wait_until='domcontentloaded')
    page.wait_for_timeout(2000)
    
    screenshot_path = r"C:\Users\ttl09\.gemini\antigravity-ide\brain\062de52b-d58f-4fd9-b45a-55e1941021f0\verified_fixed_and_working.png"
    page.screenshot(path=screenshot_path, full_page=False)
    print("Screenshot saved to", screenshot_path)
    browser.close()
