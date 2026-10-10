# -*- coding: utf-8 -*-
import asyncio
from playwright.async_api import async_playwright

async def capture_scroll_compact():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        await page.goto("http://127.0.0.1:9090", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1.5)

        # 뷰어 마운트 및 부모 스크롤 박스 끝까지 내리기
        await page.evaluate("""() => {
            const container = document.querySelector('#chat-history') || document.body;
            window.liveBrowser.mount(container, 'https://www.wikipedia.org');
            const card = document.querySelector('#browser-live-viewer-card');
            
            // 모든 스크롤 가능한 부모 찾아서 스크롤
            let el = card;
            while (el && el !== document.body) {
                if (el.scrollHeight > el.clientHeight) {
                    el.scrollTop = el.scrollHeight;
                }
                el = el.parentElement;
            }
            window.scrollTo(0, document.body.scrollHeight);
        }""")

        await asyncio.sleep(2.0)
        await page.screenshot(path="C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/browser_live_compact_view.png")
        print("✅ browser_live_compact_view.png 저장 완료")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(capture_scroll_compact())
