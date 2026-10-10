# -*- coding: utf-8 -*-
import asyncio
from playwright.async_api import async_playwright

async def capture_chat_compact():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        await page.goto("http://127.0.0.1:9090", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1.5)

        # 뷰어 마운트: 실제 메시지 컨테이너인 #chat-messages-container에 마운트
        await page.evaluate("""() => {
            const container = document.getElementById('chat-messages-container');
            if (container) {
                // 이전 카드 제거
                document.querySelector('#browser-live-viewer-card')?.remove();
                window.liveBrowser.mount(container, 'https://www.wikipedia.org');
                const card = document.querySelector('#browser-live-viewer-card');
                if (card) {
                    card.scrollIntoView({ behavior: 'instant', block: 'center' });
                }
            }
        }""")

        await asyncio.sleep(2.0)
        await page.screenshot(path="C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/browser_compact_in_chat.png")
        print("✅ browser_compact_in_chat.png 저장 완료")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(capture_chat_compact())
