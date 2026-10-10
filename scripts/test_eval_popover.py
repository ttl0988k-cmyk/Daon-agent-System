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

    btns = page.query_selector_all('.edit-provider-btn')
    if btns:
        btns[0].click()
        time.sleep(1)

        # Let's inspect popover and button in JS directly
        res = page.evaluate('''() => {
            const btn = document.querySelector('.model-effort-btn');
            if (!btn) return { error: 'no btn' };
            
            // Dispatch click
            btn.click();
            
            const pop = document.getElementById('model-row-floating-popover');
            if (!pop) return { error: 'no pop' };

            return {
                hidden: pop.classList.contains('hidden'),
                left: pop.style.left,
                top: pop.style.top,
                rect: pop.getBoundingClientRect(),
                innerHTML: pop.innerHTML.substring(0, 150),
                zIndex: window.getComputedStyle(pop).zIndex,
                display: window.getComputedStyle(pop).display,
                parentTag: pop.parentElement ? pop.parentElement.tagName : null
            };
        }''')
        print("JS Eval result:", res)

        page.screenshot(path='C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/eval_popover_test.png')

    browser.close()
