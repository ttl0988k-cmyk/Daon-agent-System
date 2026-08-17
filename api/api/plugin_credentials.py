"""
Plugin Credential Store -- DAON 플러그인 자격증명 안전 저장소.

설계 원칙 (역할 분리):
  - **사용자** 만이 키 값을 Credential Store 에 기록한다 (UI secure input).
  - **Agent** 는 ``plugin_set_secret`` 툴로 '어떤 자격증명이 필요한지'
    조회/요청(pending)하고, 미설정 키를 사용자에게 알린다.  Agent 는
    키 *값* 을 절대 수신/저장하지 않는다 (툴 스키마에 value 가 없다).
  - Store 의 값은 로그/HTTP 응답/에이전트 프롬프트에 노출되지 않는다.
    이 모듈의 모든 조회 API 는 bool(설정 여부)만 반환한다.

저장 위치:
  - PyInstaller(배포):  %LOCALAPPDATA%/Daon/credentials.json
  - 개발 모드:          <repo>/data/credentials.json
  (plugin_gateway.py 의 _resolve_user_plugins_dir() 과 동일한 경로 규칙)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

_logger = logging.getLogger(__name__)

_lock = threading.RLock()

# ---------------------------------------------------------------------------
# 경로 결정
# ---------------------------------------------------------------------------


def _resolve_credentials_dir() -> Path:
    """PyInstaller 배포 시 %LOCALAPPDATA%/Daon, 개발 시 <repo>/data."""
    try:
        if getattr(__import__("sys"), "frozen", False):
            local = os.environ.get("LOCALAPPDATA") or str(Path.home())
            return Path(local) / "Daon"
    except Exception:  # pragma: no cover - 방어적
        pass
    return Path(__file__).resolve().parent.parent / "data"


def _credentials_file() -> Path:
    return _resolve_credentials_dir() / "credentials.json"


# ---------------------------------------------------------------------------
# 내부 저장/로드
# ---------------------------------------------------------------------------


def _load() -> dict:
    """전체 credential store 를 로드한다.  값은 이 모듈 내부에서만 다룬다."""
    with _lock:
        try:
            path = _credentials_file()
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except Exception as exc:
            _logger.warning("Failed to load credentials file: %s", exc)
        return {"plugins": {}, "pending": []}


def _save(data: dict) -> None:
    with _lock:
        try:
            path = _credentials_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            tmp.replace(path)
            # Windows 에서도 소유자 외 접근 최소화 시도 (로컬 계정이 기본 소유자)
            try:
                import stat
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
        except Exception as exc:
            _logger.error("Failed to save credentials file: %s", exc)


# ---------------------------------------------------------------------------
# 공개 API — 값은 절대 외부로 반환하지 않는다
# ---------------------------------------------------------------------------


def get_credential(plugin_name: str, key: str) -> Optional[str]:
    """내부 전용 — 자격증명 값을 반환한다 (환경변수 주입 시에만 사용)."""
    with _lock:
        data = _load()
        plugins = data.get("plugins", {})
        return (plugins.get(plugin_name) or {}).get(key)


def set_credential(plugin_name: str, key: str, value: str) -> bool:
    """자격증명을 저장한다.  사용자(UI secure input)만 호출해야 한다."""
    key = (key or "").strip()
    if not plugin_name or not key:
        return False
    if value is None:
        return False
    with _lock:
        data = _load()
        plugins = data.setdefault("plugins", {})
        entry = plugins.setdefault(plugin_name, {})
        entry[key] = value
        _remove_pending(data, plugin_name, key)
        _save(data)
        return True


def delete_credential(plugin_name: str, key: str) -> bool:
    """특정 자격증명을 삭제한다."""
    key = (key or "").strip()
    if not plugin_name or not key:
        return False
    with _lock:
        data = _load()
        plugins = data.get("plugins", {})
        entry = plugins.get(plugin_name) or {}
        if key not in entry:
            return False
        del entry[key]
        if not entry:
            plugins.pop(plugin_name, None)
        _save(data)
        return True


def delete_plugin_credentials(plugin_name: str) -> bool:
    """플러그인의 모든 자격증명을 삭제한다 (플러그인 제거 시 정리)."""
    with _lock:
        data = _load()
        plugins = data.get("plugins", {})
        if plugin_name not in plugins:
            return False
        del plugins[plugin_name]
        _save(data)
        return True


def get_credential_status(
    plugin_name: str, secret_keys: Optional[List[str]] = None
) -> Dict[str, bool]:
    """설정 여부만 반환한다.  값 자체는 절대 반환하지 않는다."""
    with _lock:
        data = _load()
        entry = (data.get("plugins", {}).get(plugin_name) or {})
        if secret_keys is None:
            secret_keys = list(entry.keys())
        status: Dict[str, bool] = {}
        for key in secret_keys:
            key = (key or "").strip()
            if not key:
                continue
            status[key] = key in entry
        return status


def is_plugin_authenticated(
    plugin_name: str, secret_keys: Optional[List[str]] = None
) -> bool:
    """모든 필수 자격증명이 설정되었는지 여부 (값 노출 없음)."""
    status = get_credential_status(plugin_name, secret_keys)
    return bool(status) and all(status.values())


# ---------------------------------------------------------------------------
# Pending credential requests (Agent 가 사용자에게 입력을 요청)
# ---------------------------------------------------------------------------


def add_pending(plugin_name: str, key: str, session_id: str = "") -> bool:
    """Agent 가 '이 자격증명이 필요하다'고 요청한 것을 기록한다.

    값은 기록하지 않고 '어떤 키가 필요한지'만 기록하므로 안전하다.
    이미 설정된 키는 pending 에 등록되지 않는다.
    """
    key = (key or "").strip()
    if not plugin_name or not key:
        return False
    with _lock:
        data = _load()
        if get_credential(plugin_name, key):
            return False
        pending = data.setdefault("pending", [])
        for item in pending:
            if item.get("plugin") == plugin_name and item.get("key") == key:
                return False
        pending.append({
            "plugin": plugin_name,
            "key": key,
            "session_id": session_id or "",
        })
        _save(data)
        return True


def list_pending() -> List[dict]:
    """미해결 자격증명 요청 목록 (plugin/key/session_id 만)."""
    with _lock:
        data = _load()
        return list(data.get("pending", []) or [])


def resolve_pending(plugin_name: str, key: str) -> None:
    """자격증명 설정 시 pending 요청을 제거한다."""
    with _lock:
        data = _load()
        if _remove_pending(data, plugin_name, key):
            _save(data)


def _remove_pending(data: dict, plugin_name: str, key: str) -> bool:
    pending = data.get("pending", [])
    before = len(pending)
    data["pending"] = [
        p for p in pending
        if not (p.get("plugin") == plugin_name and p.get("key") == key)
    ]
    return len(data["pending"]) != before


def clear_all_pending() -> None:
    with _lock:
        data = _load()
        data["pending"] = []
        _save(data)
