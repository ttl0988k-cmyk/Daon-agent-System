"""
[캡챠 감지 알림] api/api/routes/browser_routes.py 패치
스냅샷에 캡챠/차단 패턴이 있으면 에이전트가 인지할 수 있게 경고를 삽입한다.
"""
import ast

FILE = 'api/api/routes/browser_routes.py'
with open(FILE, encoding='utf-8') as f:
    src = f.read()

FIND = '                        snapshot_text = _a11y_to_text(snapshot) if snapshot else ""'

REPLACE = '''                        snapshot_text = _a11y_to_text(snapshot) if snapshot else ""

                        # [2026-08-31 캡챠 감지] 스냅샷에 캡챠/차단 패턴이 있으면
                        # 에이전트가 인지하고 사용자에게 직접 해결을 요청할 수 있게
                        # 스냅샷 앞에 경고를 삽입한다.
                        try:
                            _captcha_patterns = ['captcha', 'challenge', 'verify you are human',
                                                 'are you a robot', 'confirm you are human',
                                                 '보안 확인', '자동입력 방지', 'access denied',
                                                 'unusual traffic', 'blocked']
                            _lower = snapshot_text.lower()
                            _detected = [p for p in _captcha_patterns if p in _lower]
                            if _detected:
                                snapshot_text = (
                                    "[CAPTCHA/차단 감지됨 - 패턴: " + ', '.join(_detected) + "]\\n"
                                    "[사용자에게 알리고, 내부 브라우저에서 사용자가 직접 해결하도록 요청하세요. "
                                    "해결될 때까지 다른 도구 실행을 잠시 멈추는 것이 좋습니다.]\\n\\n"
                                    + snapshot_text
                                )
                        except Exception:
                            pass'''

count = src.count(FIND)
if count != 1:
    print(f"[FAIL] 매칭 {count}회 (1회여야 함)")
    exit(1)

src = src.replace(FIND, REPLACE)
with open(FILE, 'w', encoding='utf-8') as f:
    f.write(src)

ast.parse(src)
print("[OK] 캡챠 감지 알림 패치 적용 + 문법 체크 OK")
