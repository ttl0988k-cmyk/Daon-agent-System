# -*- coding: utf-8 -*-
import asyncio
from playwright.async_api import async_playwright

async def capture_live_browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        await page.goto("http://127.0.0.1:9090", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1.5)

        # 1. 8088 백엔드 브라우저 시작
        import urllib.request, json
        req = urllib.request.Request(
            "http://127.0.0.1:8088/api/session/start",
            data=json.dumps({"url": "https://www.wikipedia.org"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print("브라우저 세션 시작 응답:", resp.read().decode())
        except Exception as e:
            print("세션 시작 예외 (무시):", e)

        # 2. 프론트엔드에서 뷰어 마운트 및 스크롤
        await page.evaluate("""() => {
            const container = document.querySelector('#chat-history') || document.body;
            window.liveBrowser.mount(container, 'https://www.wikipedia.org');
            const card = document.querySelector('#browser-live-viewer-card');
            if (card) {
                card.scrollIntoView({ behavior: 'instant', block: 'center' });
            }
        }""")

        # 프레임 스트림 수신 대기
        await asyncio.sleep(2.5)

        # 컴팩트 모드 캡처
        await page.screenshot(path="C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/browser_live_compact.png")
        print("✅ browser_live_compact.png 저장 완료")

        # 3. 직접 제어하기 클릭하여 대화면 모드로 확대
        await page.click("#browser-takeover-btn")
        await asyncio.sleep(1.5)

        # 대화면 모드 캡처
        await page.screenshot(path="C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/browser_live_takeover_expanded.png")
        print("✅ browser_live_takeover_expanded.png 저장 완료")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(capture_live_browser())
