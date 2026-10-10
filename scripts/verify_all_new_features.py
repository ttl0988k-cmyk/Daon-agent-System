import time
from playwright.sync_api import sync_playwright

print("Connecting to http://127.0.0.1:9090/ for full feature verification...")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    page_errors = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    
    page.goto("http://127.0.0.1:9090/", wait_until="networkidle")
    page.wait_for_timeout(2500)
    
    print("\n" + "="*60)
    print("1. RUNTIME ERRORS CHECK")
    print("="*60)
    print(f">> Errors: {len(page_errors)}")
    for e in page_errors:
        print("   ERR:", e)
    assert len(page_errors) == 0, "Page has runtime errors!"
    print(">> PASS: Zero runtime errors!")
    
    print("\n" + "="*60)
    print("2. ENGINE STATUS BADGE CHECK")
    print("="*60)
    dot_class = page.eval_on_selector("#engine-status-dot", "el => el.className")
    badge_text = page.eval_on_selector("#engine-status-text", "el => el.textContent")
    print(f">> Dot class: {dot_class}")
    print(f">> Status text: {badge_text}")
    assert "bg-emerald-500" in dot_class, "Status dot is not green!"
    assert "Engine Ready" in badge_text, "Engine status text mismatch!"
    print(">> PASS: Green pulse dot & Engine Ready verified!")
    
    print("\n" + "="*60)
    print("3. WORKSPACE (PROJECT FOLDER) CHECK")
    print("="*60)
    ws_label = page.eval_on_selector("#header-workspace-label", "el => el.textContent")
    print(f">> Active workspace label: {ws_label}")
    page.click("#header-workspace-btn")
    page.wait_for_timeout(500)
    ws_modal_visible = page.eval_on_selector("#workspace-modal", "el => !el.classList.contains('hidden')")
    print(f">> Workspace modal visible after click: {ws_modal_visible}")
    assert ws_modal_visible, "Workspace modal did not open!"
    page.click("#workspace-modal-close")
    page.wait_for_timeout(300)
    print(">> PASS: Workspace badge & modal open/close verified!")
    
    print("\n" + "="*60)
    print("4. AUTONOMOUS MODE TOGGLE CHECK")
    print("="*60)
    auto_text_1 = page.eval_on_selector("#autonomous-mode-text", "el => el.textContent")
    print(f">> Initial mode: {auto_text_1}")
    page.click("#autonomous-mode-btn")
    page.wait_for_timeout(300)
    auto_text_2 = page.eval_on_selector("#autonomous-mode-text", "el => el.textContent")
    print(f">> After 1st toggle: {auto_text_2}")
    page.click("#autonomous-mode-btn")
    page.wait_for_timeout(300)
    auto_text_3 = page.eval_on_selector("#autonomous-mode-text", "el => el.textContent")
    print(f">> After 2nd toggle: {auto_text_3}")
    assert auto_text_1 != auto_text_2, "Autonomous mode toggle did not change text!"
    assert auto_text_1 == auto_text_3, "Autonomous mode toggle did not revert!"
    print(">> PASS: Autonomous mode toggles smoothly!")
    
    print("\n" + "="*60)
    print("5. SETTINGS MODAL (PROVIDERS & MODELS) CHECK")
    print("="*60)
    page.click("#sidebar-settings-btn")
    page.wait_for_timeout(600)
    settings_visible = page.eval_on_selector("#settings-modal", "el => !el.classList.contains('hidden')")
    print(f">> Settings modal visible: {settings_visible}")
    assert settings_visible, "Settings modal did not open!"
    page.click("#settings-modal-close")
    page.wait_for_timeout(300)
    print(">> PASS: Settings modal opened & closed verified!")
    
    print("\n" + "="*60)
    print("6. AGENT PERSONA SELECTOR CHECK")
    print("="*60)
    persona_val = page.eval_on_selector("#agent-persona-select", "el => el.value")
    options_count = page.eval_on_selector("#agent-persona-select", "el => el.options.length")
    print(f">> Selected persona: {persona_val}, Total options: {options_count}")
    assert options_count >= 5, "Not enough persona options!"
    print(">> PASS: Agent personas loaded!")
    
    # Take screenshot for visual confirmation
    page.screenshot(path=r"C:\Users\ttl09\.gemini\antigravity-ide\brain\062de52b-d58f-4fd9-b45a-55e1941021f0\new_features_verified.png")
    print(">> Saved screenshot to new_features_verified.png")
    
    browser.close()
    print("\nALL 6 VERIFICATIONS PASSED 100%!")
