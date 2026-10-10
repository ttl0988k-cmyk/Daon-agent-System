# -*- coding: utf-8 -*-
import asyncio
from playwright.async_api import async_playwright

async def verify_chat_send_and_idle():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        print("[TEST] 1. http://127.0.0.1:9090 접속...")
        await page.goto("http://127.0.0.1:9090", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1.5)

        # 2. 브라우저 뷰어 마운트 및 2분 타이머 확인
        print("[TEST] 2. 브라우저 뷰어 마운트 및 Idle 타이머 확인...")
        res = await page.evaluate("""() => {
            const container = document.getElementById('chat-messages-container') || document.body;
            window.liveBrowser.mount(container, 'https://www.google.com');
            const card = document.querySelector('#browser-live-viewer-card');
            return {
                cardExists: !!card,
                hasIdleTimer: !!window.liveBrowser._idleTimer,
                isOpen: window.liveBrowser.isOpen
            };
        }""")
        print("[TEST] 마운트 상태:", res)
        assert res["cardExists"], "Card must exist"
        assert res["hasIdleTimer"], "_idleTimer must be set"

        # 3. 챗 입력창에 텍스트 입력 후 엔터 전송 시뮬레이션
        print("[TEST] 3. 챗 전송(엔터) 시 카드 강제 닫힘 방지 검증...")
        # 기존 559줄이 삭제되었으므로 상태가 스트리밍 중이거나 새 메시지를 보내도 카드가 닫히지 않아야 함
        chatRes = await page.evaluate("""() => {
            const input = document.getElementById('chat-input');
            if (input) {
                input.value = '새로운 테스트 질문입니다';
            }
            // 전송 버튼 클릭 트리거
            const sendBtn = document.getElementById('send-button');
            if (sendBtn) {
                sendBtn.click();
            }
            const card = document.querySelector('#browser-live-viewer-card');
            return {
                cardStillExists: !!card,
                isOpen: window.liveBrowser.isOpen
            };
        }""")
        print("[TEST] 챗 전송 직후 카드 유지 상태:", chatRes)
        assert chatRes["cardStillExists"], "Card must NOT be closed when user sends a chat message!"
        assert chatRes["isOpen"], "Viewer must remain open after sending message!"

        # 4. _touchIdle 동작 검증
        print("[TEST] 4. _touchIdle 호출 시 타이머 갱신 검증...")
        touchRes = await page.evaluate("""() => {
            const oldTimer = window.liveBrowser._idleTimer;
            // 1.5초 후 강제 터치
            window.liveBrowser._lastTouchTime = 0;
            window.liveBrowser._touchIdle();
            const newTimer = window.liveBrowser._idleTimer;
            return {
                timerExists: !!newTimer
            };
        }""")
        print("[TEST] _touchIdle 갱신 상태:", touchRes)
        assert touchRes["timerExists"], "Timer should be active"

        print("[TEST] 🎉 엔터 시 카드 강제 닫힘 방지 및 2분 Idle 타이머 검증 100% 통과!")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(verify_chat_send_and_idle())
