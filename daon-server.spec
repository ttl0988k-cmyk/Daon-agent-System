# -*- mode: python ; coding: utf-8 -*-


import os

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
    binaries=nvidia_binaries,
    datas=[('dist_new/static', 'static'), ('dist_new/api/api', 'api'), ('api/agents', 'agents'), ('dist_new/data', 'data'), ('dist_new/hermes-agent', 'hermes-agent'), ('dist_new/skills', 'skills'), ('dist_new/config.yaml', '.'), ('dist_new/index.html', '.'), ('dist_new/.env', '.env')],
    hiddenimports=['jinja2', 'markdown', 'watchfiles', 'requests', 'websockets', 'psutil', 'playwright', 'pypdf', 'PIL', 'python_multipart', 'tts_server', 'ctranslate2', 'faster_whisper', 'tokenizers', 'numpy', 'aiohttp', 'pydantic', 'yaml', 'dotenv', 'api.memory_store'],
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir='daon_runtime',
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
