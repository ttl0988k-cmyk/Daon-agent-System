# -*- coding: utf-8 -*-
"""
DAON Browser Agent Client & Process Manager
- 백엔드 브라우저 서비스(포트 8088)의 자동 시작 및 API 호출 래퍼
- 에이전트 도구(browser_tool.py / browser_bridge.py)에서 직관적으로 사용
"""
import os
import sys
import time
import shutil
import subprocess
import requests
from typing import Dict, Any, Optional

BROWSER_HOST = "127.0.0.1"
BROWSER_PORT = int(os.environ.get("BROWSER_AGENT_PORT", 8088))
BASE_URL = f"http://{BROWSER_HOST}:{BROWSER_PORT}"


def _find_server_script() -> Optional[str]:
    """server.py 위치를 개발 환경 및 패키징(설치본) 환경 모두에서 안정적으로 탐색"""
    candidates = [
        # 1. manager.py 와 동일 디렉터리
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py"),
        # 2. 설치본 resources 디렉터리
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "daon-agent-system", "resources", "browser_service", "server.py"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Daon agent System", "resources", "browser_service", "server.py"),
        # 3. 개발 워크스페이스 절대 경로
        r"c:\daon\Daon agent System\browser_service\server.py",
    ]
    # 4. sys.executable 기준 상대 경로 (server.exe 와 같은 레벨)
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "browser_service", "server.py"))
        candidates.append(os.path.join(exe_dir, "..", "resources", "browser_service", "server.py"))
    except Exception:
        pass

    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def _find_python_exe() -> str:
    """FastAPI 및 Playwright 가 설치된 Python 인터프리터 경로 탐색"""
    candidates = []
    if sys.executable and sys.executable.lower().endswith("python.exe"):
        candidates.append(sys.executable)
    
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        candidates.append(os.path.join(local_app, "Programs", "Python", "Python312", "python.exe"))
    candidates.append(r"C:\Users\ttl09\AppData\Local\Programs\Python\Python312\python.exe")
    candidates.append(shutil.which("python"))
    candidates.append(shutil.which("python3"))

    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return sys.executable or "python"


def is_service_running() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def ensure_service_running(timeout: float = 10.0) -> bool:
    if is_service_running():
        return True

    server_script = _find_server_script()
    if not server_script:
        print("[BrowserManager] 오류: browser_service/server.py 스크립트 파일을 찾을 수 없습니다.", flush=True)
        return False

    python_exe = _find_python_exe()
    print(f"[BrowserManager] 브라우저 서비스(8088) 자동 기동 중... (Python: {python_exe}, Script: {server_script})", flush=True)

    # 환경 변수 준비 (Playwright 브라우저 경로 명시적 지정)
    env = os.environ.copy()
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(local_app, "ms-playwright")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    kwargs = {}
    if sys.platform == "win32":
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = CREATE_NO_WINDOW | DETACHED_PROCESS

    try:
        proc = subprocess.Popen(
            [python_exe, server_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
            **kwargs
        )
    except Exception as e:
        print(f"[BrowserManager] 프로세스 실행 실패: {e}", flush=True)
        return False

    start_time = time.time()
    while time.time() - start_time < timeout:
        if proc.poll() is not None and proc.returncode != 0:
            print(f"[BrowserManager] 프로세스가 조기 종료되었습니다 (exit code: {proc.returncode})", flush=True)
            return False
        if is_service_running():
            print(f"[BrowserManager] ✅ 브라우저 서비스 기동 성공 (PID: {proc.pid})", flush=True)
            return True
        time.sleep(0.3)

    print("[BrowserManager] 경고: 브라우저 서비스 응답 대기 시간 초과", flush=True)
    return False


def call_browser_api(endpoint: str, data: Optional[Dict[str, Any]] = None, method: str = "POST", timeout: float = 25.0) -> Dict[str, Any]:
    if not is_service_running():
        ensure_service_running()
    url = f"{BASE_URL}/api/{endpoint.lstrip('/')}"
    try:
        if method.upper() == "GET":
            r = requests.get(url, timeout=timeout)
        else:
            r = requests.post(url, json=data or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def close_browser_session() -> Dict[str, Any]:
    if not is_service_running():
        return {"ok": True, "status": "already_closed"}
    return call_browser_api("session/close")

