import asyncio
from playwright.async_api import async_playwright

async def verify():
    print("=== [DAON] 구 버전 제공자 프리셋 및 로직 검증 시작 ===")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        url = "http://127.0.0.1:9090"
        print(f"1. {url} 접속 중...")
        await page.goto(url, wait_until='domcontentloaded', timeout=10000)
        await asyncio.sleep(2.0)

        # 2. Click Settings button to open modal
        settings_btn = await page.wait_for_selector('#sidebar-settings-btn', timeout=5000)
        assert settings_btn is not None, "#sidebar-settings-btn 없음"
        await settings_btn.click()
        await asyncio.sleep(1.0)
        print(" [OK] 설정 모달 열기 성공.")

        # 3. Click Add Provider button to show form
        add_btn = await page.wait_for_selector('#add-new-provider-btn', timeout=5000)
        assert add_btn is not None, "#add-new-provider-btn 없음"
        await add_btn.click()
        await asyncio.sleep(0.5)
        print(" [OK] 제공자 추가 폼 노출 성공.")

        # 4. Check Preset Select options
        preset_select = await page.wait_for_selector('#provider-preset-select', timeout=5000)
        assert preset_select is not None, "#provider-preset-select 없음"

        options = await page.evaluate('''() => {
            const sel = document.getElementById('provider-preset-select');
            return Array.from(sel.options).map(o => ({ value: o.value, text: o.text }));
        }''')
        print(f" 현재 프리셋 옵션 수: {len(options)}개")
        for o in options:
            print(f"   - {o['value']}: {o['text']}")

        option_keys = [o['value'] for o in options]
        expected_presets = [
            'openai', 'anthropic', 'google', 'deepseek', 'minimax',
            'openrouter', 'together', 'groq', 'xai', 'zhipu',
            'dashscope', 'qwen-token-plan', 'opencode-go', 'opencode-zen'
        ]
        for ep in expected_presets:
            assert ep in option_keys, f"필수 프리셋 '{ep}'가 누락되었습니다!"
            print(f" [OK] 구 버전 프리셋 '{ep}' 존재 확인.")

        # 5. Test Preset Selection logic (DeepSeek)
        print("5. 'deepseek' 프리셋 선택 테스트...")
        await page.select_option('#provider-preset-select', value='deepseek')
        await asyncio.sleep(0.5)

        name_val = await page.evaluate("() => document.getElementById('provider-name-input').value")
        url_val = await page.evaluate("() => document.getElementById('provider-url-input').value")
        models_val = await page.evaluate("() => document.getElementById('provider-manual-models-input').value")

        print(f"   선택 결과 -> name: '{name_val}', url: '{url_val}', models: '{models_val}'")
        assert name_val == 'deepseek', f"name 불일치: {name_val}"
        assert 'api.deepseek.com' in url_val, f"url 불일치: {url_val}"
        assert 'deepseek-chat' in models_val, f"models 불일치: {models_val}"
        print(" [OK] 'deepseek' 프리셋 로직 동작 완벽 확인.")

        # 6. Test Preset Selection logic (OpenCode Go)
        print("6. 'opencode-go' 프리셋 선택 테스트...")
        await page.select_option('#provider-preset-select', value='opencode-go')
        await asyncio.sleep(0.5)

        name_val2 = await page.evaluate("() => document.getElementById('provider-name-input').value")
        url_val2 = await page.evaluate("() => document.getElementById('provider-url-input').value")
        models_val2 = await page.evaluate("() => document.getElementById('provider-manual-models-input').value")

        print(f"   선택 결과 -> name: '{name_val2}', url: '{url_val2}', models: '{models_val2}'")
        assert name_val2 == 'opencode-go', f"name 불일치: {name_val2}"
        assert 'opencode.ai/zen/go/v1' in url_val2, f"url 불일치: {url_val2}"
        assert 'deepseek-v4.1-flash' in models_val2, f"models 불일치: {models_val2}"
        print(" [OK] 'opencode-go' 프리셋 로직 동작 완벽 확인.")

        # 7. Check refresh buttons in registered providers list
        refresh_btns = await page.query_selector_all('.refresh-provider-btn')
        print(f" [OK] 등록된 제공자 목록 내 [갱신] 버튼 {len(refresh_btns)}개 확인.")

        # 8. Take screenshot
        screenshot_path = r"C:\Users\ttl09\.gemini\antigravity-ide\brain\062de52b-d58f-4fd9-b45a-55e1941021f0\provider_presets_verified.png"
        await page.screenshot(path=screenshot_path, full_page=False)
        print(f"8. 스크린샷 캡처 완료: {screenshot_path}")

        await browser.close()
        print("=== 모든 구 버전 제공자 프리셋 및 로직 검증 완벽 통과 (PASS) ===")

if __name__ == '__main__':
    asyncio.run(verify())
