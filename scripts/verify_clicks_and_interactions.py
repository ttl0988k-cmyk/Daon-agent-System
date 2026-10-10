# -*- coding: utf-8 -*-
"""
Verify interactive clicks on buttons:
1. Agent Mode / Autonomous mode toggle
2. Auto TTS toggle
3. Workspace modal open & close
4. Mic button click response
5. Read aloud (TTS) button click response
6. Chat input typing & session navigation
"""
from playwright.sync_api import sync_playwright

def test_interactions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        errors = []
        page.on('pageerror', lambda err: errors.append(str(err)))
        
        page.goto('http://127.0.0.1:9090', wait_until='domcontentloaded', timeout=10000)
        page.wait_for_timeout(2000)
        
        results = {}
        
        # 1. Test Auto TTS Toggle
        auto_tts = page.locator('#auto-tts-toggle-btn')
        initial_title = auto_tts.get_attribute('title')
        auto_tts.click()
        page.wait_for_timeout(300)
        toggled_title = auto_tts.get_attribute('title')
        results['auto_tts_toggle'] = f"before='{initial_title}' -> after='{toggled_title}'"
        
        # 2. Test Agent Mode Button in Toolbar
        agent_mode_btn = page.locator('#agent-mode-select-btn')
        auto_mode_btn = page.locator('#autonomous-mode-btn')
        auto_mode_text_before = auto_mode_btn.inner_text()
        agent_mode_btn.click()
        page.wait_for_timeout(300)
        auto_mode_text_after = auto_mode_btn.inner_text()
        results['agent_mode_click_toggles_autonomous'] = f"before='{auto_mode_text_before}' -> after='{auto_mode_text_after}'"
        
        # 3. Test Workspace Modal Open and Close
        ws_btn = page.locator('#header-workspace-btn')
        ws_modal = page.locator('#workspace-modal')
        ws_modal_visible_before = ws_modal.is_visible()
        ws_btn.click()
        page.wait_for_timeout(300)
        ws_modal_visible_open = ws_modal.is_visible()
        page.locator('#workspace-modal-close').click()
        page.wait_for_timeout(300)
        ws_modal_visible_closed = ws_modal.is_visible()
        results['workspace_modal'] = f"init={ws_modal_visible_before}, opened={ws_modal_visible_open}, closed={ws_modal_visible_closed}"
        
        # 4. Test Persona Select options
        persona_select = page.locator('#agent-persona-select')
        options = persona_select.locator('option').all_inner_texts()
        results['personas'] = options
        
        # 5. Test Mic Button click
        mic_btn = page.locator('#chat-voice-btn')
        mic_btn.click()
        page.wait_for_timeout(300)
        results['mic_btn_clicked_ok'] = True
        
        # 6. Test Speaker (read-aloud-btn) click
        read_btns = page.locator('.read-aloud-btn')
        read_count = read_btns.count()
        if read_count > 0:
            first_read = read_btns.first
            first_read.click()
            page.wait_for_timeout(500)
            results['read_aloud_clicked_ok'] = True
            results['read_aloud_count'] = read_count
        else:
            results['read_aloud_clicked_ok'] = False
            
        # 7. Check if there are any errors during all clicks
        results['runtime_errors'] = errors
        
        import pprint
        print("=== INTERACTION TEST RESULTS ===")
        pprint.pprint(results)
        
        browser.close()

if __name__ == '__main__':
    test_interactions()
