import http.server
import socketserver
import threading
import os
import time
from playwright.sync_api import sync_playwright

PORT = 9991
web_dir = r"c:\daon\Daon agent System"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=web_dir, **kwargs)
        
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

def run_server():
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Serving at {PORT}")
        httpd.serve_forever()

t = threading.Thread(target=run_server, daemon=True)
t.start()
time.sleep(1)

print("Starting playwright to capture browser console logs and errors...")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    errors = []
    logs = []
    
    page.on("pageerror", lambda err: errors.append(f"PAGEERROR: {err}\nSTACK: {getattr(err, 'stack', 'No stack')}"))
    page.on("console", lambda msg: logs.append(f"CONSOLE [{msg.type}]: {msg.text}"))
    
    print(f"Navigating to http://127.0.0.1:{PORT}/index.html ...")
    page.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="networkidle")
    page.wait_for_timeout(3000)
    
    print("\n" + "="*50)
    print("=== CONSOLE LOGS ===")
    print("="*50)
    for log in logs:
        print(log)
        
    print("\n" + "="*50)
    print("=== PAGE RUNTIME ERRORS ===")
    print("="*50)
    for err in errors:
        print(err)
        
    # Check if elements are bound
    test_result = page.evaluate("""() => {
        const sendBtn = document.querySelector('#send-message-btn') || document.querySelector('button[type="submit"]');
        const chatInput = document.querySelector('#chat-input');
        const sidebarItems = document.querySelectorAll('.sidebar-item');
        return {
            sendBtn: !!sendBtn,
            chatInput: !!chatInput,
            sidebarCount: sidebarItems.length,
            title: document.title
        };
    }""")
    print("\nDOM Probe:", test_result)
    
    browser.close()
