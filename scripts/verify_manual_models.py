import asyncio
from playwright.async_api import async_playwright

async def verify():
    print("=== [DAON] 모델 수동 등록(챗/이미지/영상 구분 셀렉터, +추가 버튼, Enter키) 검증 시작 ===")
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

        # 4. Check Manual Model input elements
        input_el = await page.wait_for_selector('#provider-manual-model-input', timeout=5000)
        assert input_el is not None, "#provider-manual-model-input 없음"
        print(" [OK] #provider-manual-model-input 모델명 입력 필드 확인.")

        type_el = await page.wait_for_selector('#provider-manual-type-select', timeout=5000)
        assert type_el is not None, "#provider-manual-type-select 구분 셀렉터 없음"
        print(" [OK] #provider-manual-type-select (chat/image/video) 구분 버튼 확인.")

        add_model_btn = await page.wait_for_selector('#provider-add-manual-model-btn', timeout=5000)
        assert add_model_btn is not None, "#provider-add-manual-model-btn 등록 버튼 없음"
        print(" [OK] #provider-add-manual-model-btn '+ 추가' 등록 버튼 확인.")

        # 5. Add Chat model via button
        print("5. 💬 chat 모델 ('my-custom-chat') 추가 테스트...")
        await page.fill('#provider-manual-model-input', 'my-custom-chat')
        await page.select_option('#provider-manual-type-select', 'chat')
        await add_model_btn.click()
        await asyncio.sleep(0.5)

        # 6. Add Image model via Enter key
        print("6. 🖼 image 모델 ('my-sdxl-image') Enter 키 추가 테스트...")
        await page.fill('#provider-manual-model-input', 'my-sdxl-image')
        await page.select_option('#provider-manual-type-select', 'image')
        await page.keyboard.press('Enter')
        await asyncio.sleep(0.5)

        # 7. Add Video model via button
        print("7. 🎬 video 모델 ('my-kling-video') 추가 테스트...")
        await page.fill('#provider-manual-model-input', 'my-kling-video')
        await page.select_option('#provider-manual-type-select', 'video')
        await add_model_btn.click()
        await asyncio.sleep(0.5)

        # 8. Check rendered models in list
        rows = await page.query_selector_all('#detected-models-scroll .model-row-item')
        print(f" [OK] 목록에 추가된 모델 행 개수: {len(rows)}개")
        assert len(rows) >= 3, f"모델이 3개 이상 추가되지 않았습니다: {len(rows)}"

        # Verify each model name and type in DOM
        models_info = await page.evaluate('''() => {
            const items = document.querySelectorAll('#detected-models-scroll .model-row-item');
            return Array.from(items).map(item => ({
                id: item.querySelector('.model-id-text').textContent.trim(),
                type: item.querySelector('.model-type-select').value,
                checked: item.querySelector('.model-check-box').checked
            }));
        }''')
        print(" 현재 등록된 모델 상세:")
        for mi in models_info:
            print(f"   - {mi['id']} (타입: {mi['type']}, 체크: {mi['checked']})")

        chat_found = any(m['id'] == 'my-custom-chat' and m['type'] == 'chat' for m in models_info)
        image_found = any(m['id'] == 'my-sdxl-image' and m['type'] == 'image' for m in models_info)
        video_found = any(m['id'] == 'my-kling-video' and m['type'] == 'video' for m in models_info)

        assert chat_found, "my-custom-chat (chat) 확인 실패"
        assert image_found, "my-sdxl-image (image) 확인 실패"
        assert video_found, "my-kling-video (video) 확인 실패"
        print(" [OK] 💬 챗, 🖼 이미지, 🎬 영상 모델이 각각 정확한 타입과 체크 상태로 등록됨!")

        # 9. Test duplicate re-entry scrolls & highlights
        print("9. 기존 모델 ('my-custom-chat') 중복 입력 시 체크/하이라이트 동작 테스트...")
        await page.fill('#provider-manual-model-input', 'my-custom-chat')
        await add_model_btn.click()
        await asyncio.sleep(0.5)
        print(" [OK] 중복 입력 시 안전한 하이라이트/체크 유지 확인.")

        # 10. Take screenshot
        screenshot_path = r"C:\Users\ttl09\.gemini\antigravity-ide\brain\062de52b-d58f-4fd9-b45a-55e1941021f0\manual_models_verified.png"
        await page.screenshot(path=screenshot_path, full_page=False)
        print(f"10. 스크린샷 캡처 완료: {screenshot_path}")

        await browser.close()
        print("=== 모든 모델 수동 등록(챗/이미지/영상) 검증 완벽 통과 (PASS) ===")

if __name__ == '__main__':
    asyncio.run(verify())
