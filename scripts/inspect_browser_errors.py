from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        errors = []
        logs = []
        
        page.on("pageerror", lambda err: errors.append(f"PAGEERROR: {err}"))
        page.on("console", lambda msg: logs.append(f"CONSOLE [{msg.type}]: {msg.text}"))
        
        print("Navigating to http://127.0.0.1:9090/ ...")
        page.goto("http://127.0.0.1:9090/", wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        print("=== CONSOLE LOGS ===")
        for log in logs:
            print(log)
            
        print("=== PAGE ERRORS ===")
        for err in errors:
            print(err)
            
        browser.close()

if __name__ == "__main__":
    run()
