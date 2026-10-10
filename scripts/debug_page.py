# -*- coding: utf-8 -*-
import sys
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        errors = []
        logs = []
        page.on('pageerror', lambda err: errors.append(str(err)))
        page.on('console', lambda msg: logs.append(f'{msg.type}: {msg.text}'))
        
        try:
            page.goto('http://127.0.0.1:9090', wait_until='domcontentloaded', timeout=10000)
        except Exception as e:
            print("Goto error:", e)
            
        page.wait_for_timeout(2000)
        
        print("=== CONSOLE LOGS ===")
        for l in logs:
            print(l)
            
        print("\n=== PAGE RUNTIME ERRORS ===")
        for e in errors:
            print(e)
            
        res = page.evaluate('''() => {
            const centerEl = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
            const bodyStyle = window.getComputedStyle(document.body);
            
            // Check all potential full-screen blocker elements
            const blockers = Array.from(document.querySelectorAll('*')).filter(el => {
                const s = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return (s.position === 'fixed' || s.position === 'absolute') &&
                       rect.width >= window.innerWidth * 0.9 &&
                       rect.height >= window.innerHeight * 0.9 &&
                       s.display !== 'none' &&
                       s.visibility !== 'hidden' &&
                       parseFloat(s.opacity) > 0 &&
                       s.pointerEvents !== 'none';
            }).map(el => ({
                id: el.id,
                tag: el.tagName,
                cls: el.className,
                zIndex: window.getComputedStyle(el).zIndex
            }));
            
            // Check agent mode button
            const agentModeBtn = document.querySelector('#agent-mode-select-btn') || 
                                 document.querySelector('button[title*="에이전트 모드"]') ||
                                 document.querySelector('button[title*="모드"]');
                                 
            // Check mic / speaker buttons
            const micBtn = document.querySelector('#chat-voice-btn');
            const autoTtsBtn = document.querySelector('#auto-tts-toggle-btn');
            const readBtns = document.querySelectorAll('.read-aloud-btn');
            
            return {
                centerEl: centerEl ? { tag: centerEl.tagName, id: centerEl.id, cls: centerEl.className } : null,
                blockers,
                agentModeBtn: agentModeBtn ? { id: agentModeBtn.id, title: agentModeBtn.title, html: agentModeBtn.outerHTML } : null,
                micBtn: !!micBtn,
                autoTtsBtn: !!autoTtsBtn,
                readBtnsCount: readBtns.length
            };
        }''')
        
        print("\n=== DOM EVALUATION ===")
        import pprint
        pprint.pprint(res)
        
        browser.close()

if __name__ == '__main__':
    main()
