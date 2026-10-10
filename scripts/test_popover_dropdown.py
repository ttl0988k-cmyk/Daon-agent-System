from playwright.sync_api import sync_playwright
import time

print("Starting verification test...", flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    
    page.goto('http://127.0.0.1:9090')
    page.wait_for_load_state('domcontentloaded')
    time.sleep(1.5)

    print("Opening settings modal...", flush=True)
    page.click('#sidebar-settings-btn')
    time.sleep(1)

    btns = page.query_selector_all('.edit-provider-btn')
    print(f"Found {len(btns)} edit-provider-btn elements", flush=True)
    
    if btns:
        btns[0].click()
        print("Clicked edit provider", flush=True)
        time.sleep(1)

        # Scroll down so provider models form is visible
        page.evaluate('''() => {
            const scroller = document.querySelector('#settings-modal .flex-1.overflow-y-auto');
            if (scroller) scroller.scrollTop = scroller.scrollHeight;
        }''')
        time.sleep(0.5)

        effort_btns = page.query_selector_all('.model-effort-btn')
        print(f"Found {len(effort_btns)} model-effort-btn", flush=True)

        if effort_btns:
            first_btn = effort_btns[0]
            print("First btn text before click:", first_btn.text_content().strip(), flush=True)

            print("Clicking first effort btn...", flush=True)
            first_btn.click()
            time.sleep(0.5)

            popover = page.query_selector('#model-row-floating-popover')
            is_visible = popover.is_visible() if popover else False
            print("Popover visible after click:", is_visible, flush=True)

            page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/popover_open_verified.png')

            # Click 'high' inside popover
            high_opt = page.query_selector('#model-row-floating-popover button[data-value="high"]')
            if high_opt:
                print("Clicking high option in popover...", flush=True)
                high_opt.click()
                time.sleep(0.5)
                print("First btn text after click high:", first_btn.text_content().strip(), flush=True)

            # Click second model's effort btn to test 'low'
            if len(effort_btns) > 1:
                second_btn = effort_btns[1]
                print("Clicking second effort btn...", flush=True)
                second_btn.click()
                time.sleep(0.5)
                low_opt = page.query_selector('#model-row-floating-popover button[data-value="low"]')
                if low_opt:
                    print("Clicking low option in popover...", flush=True)
                    low_opt.click()
                    time.sleep(0.5)
                    print("Second btn text after click low:", second_btn.text_content().strip(), flush=True)

            # Test clicking model-type-btn to see type popover
            type_btns = page.query_selector_all('.model-type-btn')
            if type_btns:
                print("Clicking model type btn...", flush=True)
                type_btns[0].click()
                time.sleep(0.5)
                page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/type_popover_open.png')
                print("Saved type_popover_open.png", flush=True)

            page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/effort_updated_verified.png')

    browser.close()
    print("Verification completed successfully!", flush=True)
