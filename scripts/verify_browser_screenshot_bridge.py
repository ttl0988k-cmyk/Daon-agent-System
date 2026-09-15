# -*- coding: utf-8 -*-
"""브라우저 브리지 screenshot 핸들러 검증.

배경: agent-browser CLI는 `screenshot <path>` 로 파일을 직접 쓰지만,
다온(Electron) 브리지는 CDP가 돌려준 image_base64만 반환한다.
hermes browser_vision은 `screenshot_path.exists()`를 검사하므로 항상 실패했다.

이 스크립트는 api.browser_bridge._run_browser_command_via_bridge가
경로 인자를 받았을 때 실제 PNG 파일을 생성하는지 검증한다.
"""
import base64
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 개발 트리에서는 바깥 api/__init__.py 가 'api' 패키지를 선점해 중첩 사본이
# 가려진다(프로즌 배포본에는 바깥 __init__.py 가 없어 문제되지 않음).
# 따라서 소스 파일을 경로로 직접 로드해 실제 배포 동작을 재현한다.
BRIDGE_SRC = ROOT / 'api' / 'api' / 'browser_bridge.py'

# 1x1 투명 PNG
PNG_1x1 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
)

CALLS = []


def _fake_submit_task(action, **kwargs):
    CALLS.append((action, kwargs))
    if action == 'screenshot':
        return {
            '_result_id': 1,
            'status': 'ok',
            'url': 'https://example.com/',
            'image_base64': PNG_1x1,
            'labeled': kwargs.get('labeled', False),
        }
    return {'_result_id': 2, 'status': 'ok'}


def _install_fake_routes():
    """api.routes.browser_routes 를 가짜 모듈로 선점한다."""
    pkg = types.ModuleType('api.routes')
    pkg.__path__ = []  # namespace
    mod = types.ModuleType('api.routes.browser_routes')
    mod._submit_task = _fake_submit_task
    pkg.browser_routes = mod
    sys.modules['api.routes'] = pkg
    sys.modules['api.routes.browser_routes'] = mod


def main() -> int:
    results = []

    _install_fake_routes()

    spec = importlib.util.spec_from_file_location('_daon_browser_bridge', BRIDGE_SRC)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    with tempfile.TemporaryDirectory() as td:
        out_path = os.path.join(td, 'nested', 'shot.png')

        # ── 케이스 1: 경로 인자가 있으면 파일이 생성되어야 한다 ──
        res = bridge._run_browser_command_via_bridge(
            'task-1', 'screenshot', ['--full', out_path]
        )
        created = Path(out_path)
        ok1 = bool(res.get('success')) and created.exists()
        same_bytes = False
        if created.exists():
            same_bytes = created.read_bytes() == base64.b64decode(PNG_1x1)
        path_reported = (res.get('data', {}) or {}).get('path') == out_path
        results.append(('screenshot with path creates file', ok1 and same_bytes))
        results.append(('screenshot reports data.path', path_reported))

        # ── 케이스 2: 경로 인자가 없으면 기존 동작(base64만) 유지 ──
        res2 = bridge._run_browser_command_via_bridge(
            'task-2', 'screenshot', ['--full']
        )
        no_path_key = 'path' not in (res2.get('data', {}) or {})
        keeps_b64 = bool((res2.get('data', {}) or {}).get('image_base64'))
        results.append(('no-path keeps base64-only behavior', no_path_key and keeps_b64))

        # ── 케이스 3: --labeled 플래그가 그대로 전달되는지 ──
        res3 = bridge._run_browser_command_via_bridge(
            'task-3', 'screenshot', ['--labeled', os.path.join(td, 'lbl.png')]
        )
        labeled_passed = any(
            a == 'screenshot' and kw.get('labeled') for a, kw in CALLS
        )
        results.append(('labeled flag forwarded', labeled_passed))

        # ── 케이스 3b: browser_vision이 실제로 넘기는 --annotate 도 labeled로 매핑 ──
        CALLS.clear()
        bridge._run_browser_command_via_bridge(
            'task-3b', 'screenshot', ['--annotate', '--full', os.path.join(td, 'ann.png')]
        )
        annotate_passed = any(
            a == 'screenshot' and kw.get('labeled') for a, kw in CALLS
        )
        results.append(('--annotate mapped to labeled', annotate_passed))

        # ── 케이스 4: 경로가 있는데 이미지 데이터가 없으면 명시적 실패 ──
        def _empty_submit(action, **kwargs):
            if action == 'screenshot':
                return {'_result_id': 9, 'status': 'ok', 'image_base64': ''}
            return {'_result_id': 9, 'status': 'ok'}

        sys.modules['api.routes.browser_routes']._submit_task = _empty_submit
        res4 = bridge._run_browser_command_via_bridge(
            'task-4', 'screenshot', ['--full', os.path.join(td, 'empty.png')]
        )
        results.append(
            ('empty image -> explicit failure', res4.get('success') is False)
        )

    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
