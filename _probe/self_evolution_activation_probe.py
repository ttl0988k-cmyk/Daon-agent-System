# -*- coding: utf-8 -*-
"""
[갭 E-4b 2026-08-25] 자가 진화 활성화 프로브.

검증 대상:
  1. get_self_evolution_prompt_block() - 항상 비어있지 않은 블록 반환, 절대 raise 없음
  2. is_auto_mode_enabled()            - 기본 False, env 오버라이드 인식
  3. start_proposal()                  - invalid/duplicate 처리, 백그라운드 스레드 시작
  4. streaming.py 주입 코드 존재       - 툴 주입 + 프롬프트 주입 두 지점
  5. config.yaml self_evolution 섹션   - 옵트인 플래그 정의
  6. E-L2/E-L4 부품과의 시그니처 호환  - dispatch_builder_requests / incorporate_builder_dispatches

실행: python _probe/self_evolution_activation_probe.py
"""
import os
import sys
import time

# 서버와 동일한 임포트 루트: api 패키지의 부모 디렉터리(<repo>/api)를 sys.path 에 추가.
# 주의: insert(0) 이므로 나중에 넣은 쪽이 앞에 온다. 바깥쪽 <repo>/api/__init__.py 에
# 가려지지 않도록 반드시 안쪽(<repo>/api -> api/api)이 리스트 앞쪽에 위치해야 한다.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_API_PARENT = os.path.join(_ROOT, 'api')
for p in (_ROOT, _API_PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, detail))


def main():
    # ── 1. 모듈 임포트 ──
    try:
        from api.dynamic.self_evolution import (
            get_self_evolution_prompt_block,
            is_auto_mode_enabled,
            start_proposal,
        )
        check("self_evolution module imports", True)
    except Exception as e:
        check("self_evolution module imports", False, str(e))
        return

    # ── 2. 프롬프트 블록: 항상 내용 있음, raise 없음 ──
    try:
        block = get_self_evolution_prompt_block()
        check("prompt block non-empty", isinstance(block, str) and len(block) > 100)
        check("prompt block header", "[SELF-EVOLUTION CAPABILITY]" in block)
        check("prompt block mentions tool", "propose_self_evolution" in block)
        check("prompt block immutable order",
              "approve" in block and "incorporate" in block)
    except Exception as e:
        check("prompt block no-raise", False, str(e))

    # ── 3. auto_mode 기본값: config.yaml 의 false 또는 env 미설정 -> False ──
    saved_env = os.environ.pop('DAON_SELF_EVOLUTION_AUTO', None)
    try:
        check("auto_mode default False", is_auto_mode_enabled() is False)
    finally:
        if saved_env is not None:
            os.environ['DAON_SELF_EVOLUTION_AUTO'] = saved_env

    # ── 4. auto_mode env 오버라이드 ──
    os.environ['DAON_SELF_EVOLUTION_AUTO'] = '1'
    try:
        check("auto_mode env=1 -> True", is_auto_mode_enabled() is True)
    finally:
        os.environ.pop('DAON_SELF_EVOLUTION_AUTO', None)

    # ── 5. start_proposal: 빈 capability 는 invalid ──
    r = start_proposal("", "desc", session_id=None)
    check("start_proposal empty -> invalid",
          r.get("ok") is False and r.get("status") == "invalid")

    # ── 6. start_proposal: 승인자 없는 세션(None)도 스레드는 시작되고 즉시 반환 ──
    #    게이트가 거부하므로 스폰은 일어나지 않음 (리스크 5 안전 기본 확인).
    t0 = time.time()
    r1 = start_proposal("__probe_cap_a", "probe", session_id=None)
    elapsed = time.time() - t0
    check("start_proposal returns immediately",
          r1.get("ok") is True and r1.get("status") == "started" and elapsed < 2.0,
          "elapsed=%.2fs result=%s" % (elapsed, r1))

    # ── 7. duplicate 방지 ──
    r2 = start_proposal("__probe_cap_a", "probe again", session_id=None)
    check("start_proposal duplicate rejected",
          r2.get("ok") is False and r2.get("status") == "duplicate")

    # 스레드 종료 대기 (승인자 None -> 게이트 거부로 빠르게 종료).
    for _ in range(60):
        r3 = start_proposal("__probe_cap_a", "probe retry", session_id=None)
        if r3.get("status") != "duplicate":
            break
        time.sleep(0.5)
    check("proposal thread cleaned up after gate denial",
          r3.get("status") != "duplicate")

    # ── 8. streaming.py 주입 코드 존재 ──
    stream_path = os.path.join(_API_PARENT, 'api', 'streaming.py')
    with open(stream_path, 'r', encoding='utf-8') as f:
        src = f.read()
    check("tool injection present",
          "from api.dynamic.self_evolution import start_proposal as _sevo_start" in src)
    check("tool schema present", '"propose_self_evolution"' in src)
    check("registry alias present",
          '_sevo_registry.register_toolset_alias("evolution", "self-evolution")' in src)
    check("prompt injection present",
          "get_self_evolution_prompt_block as _sevo_block_fn" in src)

    # ── 9. config.yaml 옵트인 섹션 ──
    cfg_path = os.path.join(_ROOT, 'config.yaml')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg_src = f.read()
    check("config.yaml has self_evolution section", "self_evolution:" in cfg_src)
    check("config.yaml auto_mode default false",
          "auto_mode: false" in cfg_src)

    # ── 10. E-L2/E-L4 시그니처 호환 (키워드 인자 실제 전달 가능) ──
    try:
        import inspect
        from api.dynamic.builder_agent import dispatch_builder_requests
        sig = inspect.signature(dispatch_builder_requests)
        check("dispatch accepts approver kwarg", 'approver' in sig.parameters)
        from api.dynamic.builder_pipeline import incorporate_builder_dispatches
        sig2 = inspect.signature(incorporate_builder_dispatches)
        check("incorporate accepts approver+session kwargs",
              'approver' in sig2.parameters and 'session_id' in sig2.parameters)
    except Exception as e:
        check("E-L2/L4 signature compat", False, str(e))

    # ── 결과 ──
    print("")
    print("RESULT: pass=%d fail=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
