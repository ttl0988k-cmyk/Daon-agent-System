# -*- mode: python ; coding: utf-8 -*-


import os

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

nvidia_binaries = []
try:
    user_py = r'C:\Users\ttl09\AppData\Local\Programs\Python\Python312\Lib\site-packages'
    essential_dlls = ['cublas64_12.dll', 'cublasLt64_12.dll', 'cudnn64_9.dll']
    for sub in [('nvidia', 'cublas', 'bin'), ('nvidia', 'cudnn', 'bin')]:
        bdir = os.path.join(user_py, *sub)
        if os.path.isdir(bdir):
            for fn in os.listdir(bdir):
                if fn in essential_dlls:
                    nvidia_binaries.append((os.path.join(bdir, fn), '.'))
except Exception as e:
    print("Warning loading nvidia binaries:", e)

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
