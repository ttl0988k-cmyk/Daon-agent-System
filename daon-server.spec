# -*- mode: python ; coding: utf-8 -*-

import os
import sysconfig

from PyInstaller.utils.hooks import collect_all

# --- Playwright: bundle the full package (submodules + node driver data) ---
# `from playwright.sync_api import sync_playwright` fails inside the onefile
# bundle unless we collect submodules (sync_api/async_api/_impl) AND the
# driver data (node.exe + package/) that PyInstaller's hooks-contrib hook
# would normally grab. hiddenimports=['playwright'] alone is NOT enough.
try:
    _pw_datas, _pw_binaries, _pw_hidden = collect_all('playwright')
except Exception as _pw_exc:  # pragma: no cover
    print("Warning: collect_all('playwright') failed:", _pw_exc)
    _pw_datas, _pw_binaries, _pw_hidden = [], [], []

# --- CUDA DLL 번들 (2026-09-14 근본 수정) --------------------------------------
# [문제] 기존 spec 은 cublas64_12.dll(97.8MB) + cublasLt64_12.dll(637.7MB) 을
#        무조건 server.exe 에 포함시켰다. 실측 결과 server.exe 1,158MB 중
#        CUDA 가 735MB(63%) 를 차지했고, 그 결과:
#          ① NSIS 인스톨러가 1,298MB 로 비대해져 7z/zip 해제 단계에서
#             설치 진행률 ~10% 멈춤 (installer.nsh 주석에 기록된 실제 증상)
#          ② 첫 실행 시 _MEI 추출이 25~30초 소요
#          ③ 지인 배포 시 1.2GB 전송 부담
#        또한 사용자 경로(C:\Users\ttl09\...)를 하드코딩하고 있어 다른 PC 에서
#        빌드하면 CUDA 가 조용히 누락됐다.
#
# [해결] CUDA 를 선택 사항으로 만든다. 기본은 번들 제외(slim).
#        · 개발 PC(로컬 whisper GPU 가속 필요) → DAON_CUDA=1 로 빌드
#        · 배포본(지인, GPU 불필요)            → 기본값 그대로 (자동 slim)
#
# [왜 빼도 되는가] whisper_routes.py 의 _register_nvidia_cuda_dlls() 가
#        exe_dir → site-packages/nvidia/... → sys._MEIPASS 순서로 DLL 을 찾는다.
#        개발 PC 에는 nvidia-cublas-cu12 / nvidia-cudnn-cu12 가 이미 설치돼
#        있으므로 번들에서 빠져도 GPU 가속이 유지된다. (없으면 CPU 로 자동 폴백)
_INCLUDE_CUDA = os.environ.get('DAON_CUDA', '0').strip().lower() in ('1', 'true', 'yes', 'on')

nvidia_binaries = []
if _INCLUDE_CUDA:
    essential_dlls = ['cublas64_12.dll', 'cublasLt64_12.dll', 'cudnn64_9.dll']
    # 하드코딩된 사용자 경로 대신 실제 인터프리터의 site-packages 를 조회한다.
    bases = [
        os.environ.get('DAON_CUDA_DIR'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''),
                     'Programs', 'Python', 'Python312', 'Lib', 'site-packages'),
        sysconfig.get_paths().get('purelib'),
    ]
    _seen = set()
    for _base in bases:
        if not _base or _base in _seen or not os.path.isdir(_base):
            continue
        _seen.add(_base)
        for _sub in (('nvidia', 'cublas', 'bin'), ('nvidia', 'cudnn', 'bin')):
            _bdir = os.path.join(_base, *_sub)
            if not os.path.isdir(_bdir):
                continue
            for _fn in os.listdir(_bdir):
                if _fn in essential_dlls:
                    nvidia_binaries.append((os.path.join(_bdir, _fn), '.'))
    print(f"[spec] CUDA DLL bundled: {len(nvidia_binaries)} file(s)")
else:
    print("[spec] CUDA skipped — slim build (set DAON_CUDA=1 to bundle CUDA)")

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=nvidia_binaries + _pw_binaries,
    # The packaged app keeps the original DAON HTML workspace. The Roo React
    # composition is not a replacement frontend and must not be embedded as
    # the server's primary UI.
    datas=[('dist_new/static', 'static'), ('dist_new/api/api', 'api'), ('api/agents', 'agents'), ('dist_new/hermes-agent', 'hermes-agent'), ('dist_new/skills', 'skills'), ('dist_new/config.yaml', '.'), ('dist_new/index.html', '.')] + _pw_datas,
    hiddenimports=['jinja2', 'markdown', 'watchfiles', 'requests', 'websockets', 'psutil', 'playwright', 'pypdf', 'PIL', 'python_multipart', 'tts_server', 'ctranslate2', 'faster_whisper', 'tokenizers', 'numpy', 'aiohttp', 'pydantic', 'yaml', 'dotenv', 'api.memory_store'] + _pw_hidden + ['playwright.sync_api', 'playwright.async_api'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir='daon_runtime',
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
