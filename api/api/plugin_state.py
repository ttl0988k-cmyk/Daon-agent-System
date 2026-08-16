"""
Plugin State — DAON Plugin ON/OFF + 세션(탭) 단위 스코프 상태 저장소.

설계 (세션/탭 스코프 모델):
  - 전역 활성화(global_enabled): 플러그인 설치/제거 차원의 ON/OFF.
    OFF면 어떤 세션에서도 로드되지 않는다.
  - 세션 스코프(sessions): session_id → [plugin_name, ...]
    특정 채팅 탭/세션에서만 플러그인을 활성화한다.
    Dynamic Harness 실행 시 start_harness_job이 session_id로 이 목록을 조회해
    해당 플러그인의 스킬을 forced_skills에 자동 병합한다.
    탭을 전환하거나 OFF하면 해당 세션에서만 제거된다 (타 세션 영향 없음).

상태 파일: data/plugins_state.json (dev: 프로젝트 루트 data/, PyInstaller: exe 옆 resources/data/)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

_logger = logging.getLogger(__name__)

_lock = threading.RLock()


def _resolve_data_dir() -> Path:
    """Resolve the data/ directory next to the project root (PyInstaller-aware)."""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller: data/ 는 exe 옆 resources/ 아래에 복사된다.
        base = Path(sys.executable).parent.resolve()
        data_dir = base / "data"
        if not data_dir.exists():
            data_dir = Path(sys._MEIPASS) / "data"
    else:
        # dev: api/api/plugin_state.py → api/ → project root
        base = Path(__file__).resolve().parent.parent.parent
        data_dir = base / "data"
    return data_dir


def _state_file() -> Path:
    return _resolve_data_dir() / "plugins_state.json"


def _default_state() -> dict:
    return {
        "global_enabled": {},   # plugin_name -> bool (전역 ON/OFF)
        "sessions": {},         # session_id -> [plugin_name, ...]
    }


def _load_state() -> dict:
    try:
        f = _state_file()
        if f.exists():
            raw = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base = _default_state()
                base.update(raw)
                return base
    except Exception as exc:
        _logger.warning("Failed to load plugin state, using defaults: %s", exc)
    return _default_state()


def _save_state(state: dict) -> None:
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        _logger.error("Failed to save plugin state: %s", exc)


# ---------------------------------------------------------------------------
# 전역 ON/OFF
# ---------------------------------------------------------------------------

def is_plugin_globally_enabled(plugin_name: str) -> bool:
    """전역 활성화 여부. 명시적으로 True 로 설정된 경우에만 True."""
    with _lock:
        return bool(_load_state().get("global_enabled", {}).get(plugin_name, False))


def set_plugin_global_enabled(plugin_name: str, enabled: bool) -> dict:
    """전역 ON/OFF 를 저장하고 갱신된 상태를 반환한다."""
    with _lock:
        state = _load_state()
        state.setdefault("global_enabled", {})[plugin_name] = bool(enabled)
        if not enabled:
            # 전역 OFF → 모든 세션 스코프에서도 제거
            for sid in list(state.get("sessions", {}).keys()):
                state["sessions"][sid] = [p for p in state["sessions"].get(sid, []) if p != plugin_name]
        _save_state(state)
        return state


# ---------------------------------------------------------------------------
# 세션(탭) 단위 스코프
# ---------------------------------------------------------------------------

def get_session_plugins(session_id: str) -> list[str]:
    """특정 세션에서 활성화된 플러그인 이름 목록 (전역 OFF는 제외)."""
    with _lock:
        state = _load_state()
        names = state.get("sessions", {}).get(session_id, [])
        return [
            n for n in names
            if is_plugin_globally_enabled(n)
        ]


def set_session_plugin(session_id: str, plugin_name: str, enabled: bool) -> dict:
    """세션 스코프에서 플러그인을 ON/OFF 한다."""
    if not session_id:
        return _load_state()
    with _lock:
        state = _load_state()
        state.setdefault("sessions", {}).setdefault(session_id, [])
        cur = state["sessions"][session_id]
        if enabled:
            if plugin_name not in cur:
                cur.append(plugin_name)
                # 전역이 아직 ON 이 아니면 자동으로 켠다 (세션에서 켠다는 뜻)
                if not is_plugin_globally_enabled(plugin_name):
                    state.setdefault("global_enabled", {})[plugin_name] = True
        else:
            state["sessions"][session_id] = [p for p in cur if p != plugin_name]
        _save_state(state)
        return state


def clear_session(session_id: str) -> None:
    """세션 종료 시 스코프 상태 정리."""
    with _lock:
        state = _load_state()
        state.get("sessions", {}).pop(session_id, None)
        _save_state(state)


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

def get_all_plugins_state() -> dict:
    """전체 상태 스냅샷 (프론트 렌더링용)."""
    with _lock:
        return _load_state()
