from playwright.sync_api import sync_playwright
import time
import json
import urllib.request

print("Starting save flow test...", flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    
    page.goto('http://127.0.0.1:9090')
    page.wait_for_load_state('domcontentloaded')
    time.sleep(1)

    # Automatically accept dialogs/alerts
    page.on("dialog", lambda dialog: dialog.accept())

    # Open settings modal
    page.click('#sidebar-settings-btn')
    time.sleep(1)

    btns = page.query_selector_all('.edit-provider-btn')
    if btns:
        btns[0].click()
        time.sleep(1)

        # Scroll down so provider models form is visible
        page.evaluate('''() => {
            const scroller = document.querySelector('#settings-modal .flex-1.overflow-y-auto');
            if (scroller) scroller.scrollTop = scroller.scrollHeight;
        }''')
        time.sleep(0.5)

        # Change first model effort to 'medium'
        effort_btns = page.query_selector_all('.model-effort-btn')
        print(f"Found {len(effort_btns)} effort buttons", flush=True)
        if effort_btns:
            effort_btns[0].click()
            time.sleep(0.3)
            med_item = page.query_selector('#model-row-floating-popover button[data-value="medium"]')
            if med_item:
                med_item.click()
                time.sleep(0.3)
                print("Changed first model effort to medium", flush=True)

        # Click save button
        save_btn = page.query_selector('#provider-form-save-btn')
        if save_btn:
            save_btn.click()
            time.sleep(1)
            print("Clicked save button", flush=True)

    browser.close()

# Verify via API
try:
    req = urllib.request.urlopen('http://127.0.0.1:9090/api/providers')
    data = json.loads(req.read().decode('utf-8'))
    first_provider = list(data.keys())[0]
    models = data[first_provider].get('models', [])
    print(f"Provider: {first_provider}, Models: {json.dumps(models[:2], ensure_ascii=False)}", flush=True)
except Exception as e:
    print("API verification error:", e, flush=True)
