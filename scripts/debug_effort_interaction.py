from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.goto('http://127.0.0.1:9090')
    page.wait_for_load_state('domcontentloaded')
    time.sleep(1)

    page.click('#sidebar-settings-btn')
    time.sleep(1)

    edit_btn = page.query_selector('.edit-provider-btn')
    if edit_btn:
        edit_btn.click()
        time.sleep(1)

        # Scroll the modal content so provider-form-card is visible
        modal_content = page.query_selector('#settings-modal .flex-1.overflow-y-auto')
        if modal_content:
            modal_content.evaluate('el => el.scrollTop = el.scrollHeight')
            time.sleep(1)

        effort_selects = page.query_selector_all('.model-effort-select')
        print(f"Found {len(effort_selects)} effort selects")
        
        if effort_selects:
            first = effort_selects[0]
            box = first.bounding_box()
            print("Effort select box:", box)

            # Check if clicking it directly triggers focus or popup
            first.click()
            time.sleep(0.5)

            # Check properties
            info = first.evaluate('''el => {
                return {
                    tagName: el.tagName,
                    selectedIndex: el.selectedIndex,
                    options: Array.from(el.options).map(o => ({ text: o.text, value: o.value, selected: o.selected })),
                    rect: el.getBoundingClientRect(),
                    computedWidth: window.getComputedStyle(el).width,
                    computedHeight: window.getComputedStyle(el).height,
                    overflow: window.getComputedStyle(el.parentElement).overflow
                }
            }''')
            print("Info:", info)

            page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/modal_scroll_effort.png')

    browser.close()
