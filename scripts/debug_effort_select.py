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

    page.wait_for_selector('.edit-provider-btn', timeout=5000)

    # Find edit button for first provider
    edit_btn = page.query_selector('.edit-provider-btn')
    print('Edit button found:', edit_btn is not None)
    if edit_btn:
        edit_btn.click()
        time.sleep(1)

        selects = page.query_selector_all('.model-effort-select')
        print('model-effort-select count:', len(selects))
        if selects:
            first = selects[0]
            box = first.bounding_box()
            print('Bounding box:', box)
            is_visible = first.is_visible()
            is_enabled = first.is_enabled()
            html = first.evaluate('el => el.outerHTML')
            print('Visible:', is_visible, 'Enabled:', is_enabled)
            print('HTML:', html)

            styles = first.evaluate('''el => {
                const s = window.getComputedStyle(el);
                return {
                    pointerEvents: s.pointerEvents,
                    display: s.display,
                    zIndex: s.zIndex,
                    position: s.position,
                    opacity: s.opacity,
                    cursor: s.cursor
                };
            }''')
            print('Computed style:', styles)

            element_at_point = page.evaluate('''() => {
                const el = document.querySelector('.model-effort-select');
                if (!el) return 'none';
                const r = el.getBoundingClientRect();
                const topEl = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
                return topEl ? (topEl.tagName + '.' + topEl.className) : 'null';
            }''')
            print('Element at point:', element_at_point)

            try:
                first.click()
                print('Click succeeded!')
            except Exception as e:
                print('Click failed:', e)

            # Test selecting option 'high'
            try:
                first.select_option('high')
                val = first.evaluate('el => el.value')
                print('Selected option value:', val)
            except Exception as e:
                print('Select option failed:', e)

    # Take screenshot of the form
    page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/debug_effort_modal.png')
    print('Screenshot saved!')
    browser.close()
