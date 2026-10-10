# -*- coding: utf-8 -*-
import asyncio
from playwright.async_api import async_playwright

async def verify():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        print("[TEST] 1. http://127.0.0.1:9090 접속...")
        await page.goto("http://127.0.0.1:9090", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2.0)

        print("[TEST] 2. 브라우저 뷰어 마운트 시뮬레이션...")
        # liveBrowser.mount를 직접 호출하여 뷰어 카드 생성
        res = await page.evaluate("""() => {
            const container = document.querySelector('#chat-history') || document.body;
            window.liveBrowser.mount(container, 'https://www.google.com');
            const card = document.querySelector('#browser-live-viewer-card');
            return {
                cardExists: !!card,
                isExpanded: window.liveBrowser.isExpanded,
                isTakeover: window.liveBrowser.isTakeover,
                badgeText: document.querySelector('#browser-badge-text')?.textContent,
                takeoverBtnText: document.querySelector('#browser-takeover-btn-label')?.textContent,
                expandBtnText: document.querySelector('#browser-expand-btn-label')?.textContent
            };
        }""")
        print("[TEST] 기본 마운트 상태:", res)
        assert res["cardExists"], "Card must exist"
        assert not res["isExpanded"], "Should start in compact mode for agent"
        assert not res["isTakeover"], "Should start in agent mode"

        await page.screenshot(path="C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/browser_step1_compact.png")
        print("[TEST] Step 1 스크린샷 저장 완료 (기본 컴팩트 모드)")

        # 3. 직접 제어하기 버튼 클릭
        print("[TEST] 3. 직접 제어하기 버튼 클릭...")
        await page.click("#browser-takeover-btn")
        await asyncio.sleep(0.5)

        takeoverRes = await page.evaluate("""() => {
            const card = document.querySelector('#browser-live-viewer-card');
            const backdrop = document.querySelector('#browser-viewer-backdrop');
            const banner = document.querySelector('#browser-takeover-banner');
            return {
                isExpanded: window.liveBrowser.isExpanded,
                isTakeover: window.liveBrowser.isTakeover,
                badgeText: document.querySelector('#browser-badge-text')?.textContent,
                takeoverBtnText: document.querySelector('#browser-takeover-btn-label')?.textContent,
                hasBackdrop: !!backdrop,
                bannerVisible: !banner.classList.contains('hidden'),
                cardHasFixed: card.classList.contains('fixed')
            };
        }""")
        print("[TEST] 직접 제어 상태 (대화면 자동 확대):", takeoverRes)
        assert takeoverRes["isTakeover"], "Takeover must be active"
        assert takeoverRes["isExpanded"], "Should automatically expand to large screen when taking over"
        assert takeoverRes["hasBackdrop"], "Backdrop should exist in large screen mode"
        assert takeoverRes["bannerVisible"], "Takeover banner must be visible"

        await page.screenshot(path="C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/browser_step2_takeover_expanded.png")
        print("[TEST] Step 2 스크린샷 저장 완료 (직접 제어 & 대화면 확대 모드)")

        # 4. 줌 인 버튼 클릭 (125%)
        print("[TEST] 4. 줌 인 버튼 클릭...")
        await page.click("#browser-zoom-in-btn")
        await asyncio.sleep(0.3)
        zoomRes = await page.evaluate("""() => {
            return {
                zoom: window.liveBrowser.zoom,
                zoomLabel: document.querySelector('#browser-zoom-label')?.textContent
            };
        }""")
        print("[TEST] 줌 배율 상태:", zoomRes)
        assert zoomRes["zoom"] == 1.25, "Zoom should be 1.25"
        assert zoomRes["zoomLabel"] == "125%", "Zoom label should be 125%"

        # 5. 에이전트 제어로 복귀 버튼 클릭
        print("[TEST] 5. 에이전트 제어로 복귀 버튼 클릭...")
        await page.click("#browser-takeover-btn")
        await asyncio.sleep(0.5)

        returnRes = await page.evaluate("""() => {
            const card = document.querySelector('#browser-live-viewer-card');
            const backdrop = document.querySelector('#browser-viewer-backdrop');
            return {
                isExpanded: window.liveBrowser.isExpanded,
                isTakeover: window.liveBrowser.isTakeover,
                hasBackdrop: !!backdrop,
                cardHasFixed: card.classList.contains('fixed')
            };
        }""")
        print("[TEST] 에이전트 모드 복귀 상태:", returnRes)
        assert not returnRes["isTakeover"], "Takeover should be false"
        assert not returnRes["isExpanded"], "Should return to compact mode"
        assert not returnRes["hasBackdrop"], "Backdrop should be removed"

        # 6. onTurnCompleted 및 자동 닫힘 방지 검증 (4초 후에도 살아있는지 확인)
        print("[TEST] 6. onTurnCompleted 호출 후 화면 유지 검증...")
        await page.evaluate("""() => {
            window.liveBrowser.onTurnCompleted();
        }""")
        # 4초 대기 (기존 2.5초 자동 닫힘 타이머를 훌쩍 넘김)
        await asyncio.sleep(4.0)

        persistentRes = await page.evaluate("""() => {
            const card = document.querySelector('#browser-live-viewer-card');
            const badgeText = document.querySelector('#browser-badge-text')?.textContent;
            return {
                cardStillExists: !!card,
                isOpen: window.liveBrowser.isOpen,
                badgeText: badgeText
            };
        }""")
        print("[TEST] 4초 경과 후 화면 유지 상태:", persistentRes)
        assert persistentRes["cardStillExists"], "Card must NOT be destroyed after turn completion!"
        assert persistentRes["isOpen"], "Viewer must remain open!"
        assert "완료" in persistentRes["badgeText"], "Badge should indicate completion"

        await page.screenshot(path="C:/Users/ttl09/.gemini/antigravity-ide/brain/062de52b-d58f-4fd9-b45a-55e1941021f0/browser_step3_turn_completed_persisted.png")
        print("[TEST] Step 3 스크린샷 저장 완료 (턴 종료 후에도 온전히 화면 유지)")

        # 7. 사용자가 직접 [닫기] 클릭 시만 정리되는지 확인
        print("[TEST] 7. 사용자 직접 [닫기] 클릭 시 정리 검증...")
        await page.click("#browser-viewer-close-btn")
        await asyncio.sleep(0.5)

        closeRes = await page.evaluate("""() => {
            const card = document.querySelector('#browser-live-viewer-card');
            return {
                cardExists: !!card,
                isOpen: window.liveBrowser.isOpen
            };
        }""")
        print("[TEST] 수동 닫기 후 상태:", closeRes)
        assert not closeRes["cardExists"], "Card should be removed upon manual close"
        assert not closeRes["isOpen"], "isOpen should be false"

        print("[TEST] 🎉 모든 테스트 케이스 100% 통과!")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(verify())
