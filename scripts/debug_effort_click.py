from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.goto('http://127.0.0.1:9090')
    page.wait_for_load_state('domcontentloaded')
    time.sleep(2)

    page.click('#sidebar-settings-btn')
    time.sleep(1)

    edit_btn = page.query_selector('.edit-provider-btn')
    if edit_btn:
        edit_btn.click()
        time.sleep(1)

        # Check model-type-select vs model-effort-select
        type_select = page.query_selector('.model-type-select')
        effort_select = page.query_selector('.model-effort-select')
        
        print("Type select:", type_select.bounding_box() if type_select else None)
        print("Effort select:", effort_select.bounding_box() if effort_select else None)
        
        # Click on effort select
        if effort_select:
            box = effort_select.bounding_box()
            page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
            time.sleep(0.5)
            page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/test_click_effort.png')
            print("Clicked at", box['x'] + box['width']/2, box['y'] + box['height']/2)

    browser.close()
