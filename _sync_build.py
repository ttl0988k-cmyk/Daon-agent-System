#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""빌드용: dist_new 미러를 소스에서 재구성/동기화.

- 불안정한 워킹 트리(파일 실종)에 대비해 동기화 직전 '삭제된' 추적 파일만 복원.
  ⚠ 수정된 파일은 절대 건드리지 않는다 — 미커밋 패치가 유실되면 안 되기 때문.
  (과거 무조건 git restore api/api가 미커밋 패치를 전부 되돌린 사고가 있었음)
- WinError 183(대상 이미 존재)을 피하기 위해 dirs_exist_ok=True 사용 (rmtree 우회).
"""
import shutil
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)


def _p(rel):
    return os.path.join(ROOT, rel.replace('/', os.sep))


# 0) 워킹 트리에서 '삭제된' 추적 파일만 복원 (수정된 파일은 보존 — 미커밋 작업 보호)
print('[STEP 0] restore DELETED tracked files only (modifications preserved) ...')
try:
    _r = subprocess.run(['git', 'ls-files', '--deleted', 'api/api'],
                        capture_output=True, text=True, check=False, cwd=ROOT)
    _deleted = [ln.strip() for ln in (_r.stdout or '').splitlines() if ln.strip()]
    if _deleted:
        for _f in _deleted:
            subprocess.run(['git', 'restore', '--', _f], check=False, cwd=ROOT)
            print('   restored deleted file:', _f)
    else:
        print('   no deleted files - nothing to restore.')
except Exception as e:
    print('   git restore warn:', e)

# 1) 디렉터리 쌍 (source -> dest)
DIR_PAIRS = [
    ('static', 'dist_new/static'),
    ('api/api', 'dist_new/api/api'),
    ('data', 'dist_new/data'),
    ('hermes-agent', 'dist_new/hermes-agent'),
    ('skills', 'dist_new/skills'),
]

# 1-1) webview 빌드 산출물 (Vite dist) → dist_new/webview
#      패키징 시 webview/ 루트에 dist 내용이 그대로 들어가야 server.py의
#      WEBVIEW_DIR(_MEIPASS/webview) 탐지가 동작한다. .map은 제외.
WEBVIEW_SRC = _p('webview/dist')
WEBVIEW_DST = _p('dist_new/webview')

# 2) 개별 파일 쌍
FILE_PAIRS = [
    ('config.yaml', 'dist_new/config.yaml'),
    ('index.html', 'dist_new/index.html'),
    ('.env', 'dist_new/.env'),
]

ok, fail = [], []

for src, dst in DIR_PAIRS:
    s, d = _p(src), _p(dst)
    try:
        if not os.path.isdir(s):
            raise FileNotFoundError('source missing: ' + s)
        os.makedirs(d, exist_ok=True)
        shutil.copytree(s, d, dirs_exist_ok=True)
        ok.append(dst)
        print('[OK ]', dst)
    except Exception as e:
        fail.append((dst, str(e)))
        print('[FAIL]', dst, '::', e)

try:
    if not os.path.isdir(WEBVIEW_SRC):
        raise FileNotFoundError('source missing: ' + WEBVIEW_SRC + ' (run: cd webview && npm run build)')
    os.makedirs(WEBVIEW_DST, exist_ok=True)
    shutil.copytree(WEBVIEW_SRC, WEBVIEW_DST, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('*.map'))
    ok.append('dist_new/webview')
    print('[OK ] dist_new/webview')
except Exception as e:
    fail.append(('dist_new/webview', str(e)))
    print('[FAIL] dist_new/webview ::', e)

for src, dst in FILE_PAIRS:
    s, d = _p(src), _p(dst)
    try:
        if not os.path.isfile(s):
            raise FileNotFoundError('source missing: ' + s)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(s, d)
        ok.append(dst)
        print('[OK ]', dst)
    except Exception as e:
        fail.append((dst, str(e)))
        print('[FAIL]', dst, '::', e)

# 3) 핵심 파일 존재 검증
print('\n[VERIFY] core files in dist_new:')
for rel in ['dist_new/api/api/memory_store.py',
            'dist_new/api/api/streaming.py',
            'dist_new/api/api/routes/__init__.py',
            'dist_new/api/api/routes/admin_routes.py',
            'dist_new/static/modules/panels.js']:
    print('   ', rel, os.path.isfile(_p(rel)))

print('\nSUMMARY ok=%d fail=%d' % (len(ok), len(fail)))
sys.exit(1 if fail else 0)
