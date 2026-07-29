#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""빌드용: dist_new 미러를 소스에서 재구성/동기화.

- 불안정한 워킹 트리(파일 실종)에 대비해 동기화 직전 git restore를 먼저 실행.
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


# 0) 워킹 트리에서 실종된 추적 파일을 먼저 복원
print('[STEP 0] git restore api/api ...')
try:
    subprocess.run(['git', 'restore', 'api/api'], check=False, cwd=ROOT)
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
