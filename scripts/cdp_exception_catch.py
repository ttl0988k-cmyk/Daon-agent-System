import http.server
import socketserver
import threading
import json
import time
from playwright.sync_api import sync_playwright

PORT = 9992
web_dir = r"c:\daon\Daon agent System"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=web_dir, **kwargs)
        
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

def run_server():
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        httpd.serve_forever()

t = threading.Thread(target=run_server, daemon=True)
t.start()
time.sleep(1)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    cdp = page.context.new_cdp_session(page)
    cdp.send("Runtime.enable")
    
    exceptions = []
    
    def on_exception(event):
        exceptions.append(event)
        
    cdp.on("Runtime.exceptionThrown", on_exception)
    
    page.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="networkidle")
    page.wait_for_timeout(2000)
    
    print("=== CDP EXCEPTIONS ===")
    for ex in exceptions:
        print(json.dumps(ex, indent=2))
        
    browser.close()
