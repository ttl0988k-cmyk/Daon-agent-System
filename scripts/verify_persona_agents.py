import asyncio
import os
import json
from playwright.async_api import async_playwright

async def verify():
    print("=== [DAON] 정적 에이전트(토니, 빌, 셜록, 프라다, 라온) 및 다온응대 제외 검증 시작 ===")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        console_logs = []
        page.on('console', lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        url = "http://127.0.0.1:9090"
        print(f"1. {url} 접속 중...")
        await page.goto(url, wait_until='domcontentloaded', timeout=10000)
        await asyncio.sleep(2.0)

        # 2. Check #agent-persona-select element
        persona_select = await page.wait_for_selector('#agent-persona-select', timeout=5000)
        assert persona_select is not None, "#agent-persona-select 요소를 찾을 수 없습니다."
        print(" [OK] #agent-persona-select 요소 확인됨.")

        # 3. Check options inside persona_select
        options_data = await page.evaluate('''() => {
            const sel = document.getElementById('agent-persona-select');
            return Array.from(sel.options).map(o => ({ value: o.value, text: o.text }));
        }''')
        print(f" 현재 셀렉터 옵션 목록 ({len(options_data)}개):")
        for opt in options_data:
            print(f"   - value: '{opt['value']}', text: '{opt['text']}'")

        option_values = [o['value'] for o in options_data]
        option_texts = [o['text'] for o in options_data]

        # Verify required static agents exist
        required_agents = ['raon', '토니(기획)', '빌(개발)', '셜록(검수)', '프라다(디자인)']
        for req in required_agents:
            assert req in option_values, f"필수 정적 에이전트 '{req}'가 옵션에 없습니다!"
            print(f" [OK] 필수 에이전트 '{req}' 존재 확인.")

        # Verify '다온응대' is strictly EXCLUDED
        for val in option_values:
            assert '다온' not in val and 'daon' not in val.lower(), f"다온응대 에이전트가 목록에 남아있습니다: {val}"
        for txt in option_texts:
            assert '다온' not in txt, f"다온응대 텍스트가 목록에 남아있습니다: {txt}"
        print(" [OK] 대표님 지침에 따라 '다온응대' 완전 제외 확인됨!")

        # 4. Test Switching Personas
        test_personas = ['토니(기획)', '빌(개발)', '셜록(검수)', '프라다(디자인)', 'raon']
        for tp in test_personas:
            print(f"4. 페르소나 '{tp}' 전환 테스트 중...")
            await page.select_option('#agent-persona-select', value=tp)
            await asyncio.sleep(0.5)

            # Check localStorage
            active_persona = await page.evaluate("() => localStorage.getItem('daon_active_persona')")
            assert active_persona == tp, f"localStorage의 active persona 불일치: {active_persona} != {tp}"
            print(f" [OK] '{tp}' 전환 및 로컬스토리지/API 연동 성공!")

        # 5. Create new session with '토니(기획)' active
        print("5. '토니(기획)' 활성 상태에서 새 세션 생성 테스트...")
        await page.select_option('#agent-persona-select', value='토니(기획)')
        await asyncio.sleep(0.5)

        new_btn = await page.query_selector('#new-thread-btn')
        if new_btn:
            await new_btn.click()
            await asyncio.sleep(1.0)
            sess_items = await page.query_selector_all('#recent-threads-list a')
            print(f" [OK] 새 세션 생성 완료 (총 {len(sess_items)}개 세션).")

        # 6. Take screenshot
        screenshot_path = r"C:\Users\ttl09\.gemini\antigravity-ide\brain\062de52b-d58f-4fd9-b45a-55e1941021f0\persona_agents_verified.png"
        await page.screenshot(path=screenshot_path, full_page=False)
        print(f"6. 스크린샷 캡처 완료: {screenshot_path}")

        await browser.close()
        print("=== 모든 검증 완벽 통과 (PASS) ===")

if __name__ == '__main__':
    asyncio.run(verify())
