# -*- coding: utf-8 -*-
"""
DAON 자가 진화 및 재기동 원장 (Evolution Ledger)
==================================================
에이전트의 자가 수리, 코드 수정, self.exe 재빌드, 서버 재기동 이력을
영속적으로 기록하고, 서버 재기동 후 에이전트의 기억(Handover Memory)을
시스템 프롬프트에 자동으로 주입하여 에이전트의 기억 상실을 방지한다.

설계 원칙:
- 순수 부가(pure additive): 어떤 함수도 절대 예외를 발생시키지 않는다.
- 데이터 저장소: STATE_DIR/evolution_ledger.json (및 repo/data 미러)
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_LEDGER_FILE_NAME = "evolution_ledger.json"
_lock = threading.Lock()


def _get_target_dirs() -> List[Path]:
    """가능한 STATE_DIR 후보 디렉터리 목록 반환."""
    dirs: List[Path] = []
    try:
        from api.config import STATE_DIR
        dirs.append(Path(STATE_DIR))
    except Exception:
        pass

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        p = Path(local_app_data) / "DAON Agent System" / "data"
        if p not in dirs:
            dirs.append(p)

    # 개발 환경 repo root data 디렉터리
    try:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        dev_data = repo_root / "data"
        if dev_data not in dirs:
            dirs.append(dev_data)
    except Exception:
        pass

    return dirs


def _load_ledger_data() -> Dict[str, Any]:
    """가장 최신의 원장 데이터를 읽어 반환."""
    for d in _get_target_dirs():
        f = d / _LEDGER_FILE_NAME
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    return {
        "version": 1,
        "last_restart": None,
        "history": []
    }


def _save_ledger_data(data: Dict[str, Any]) -> bool:
    """원장 데이터를 모든 타겟 디렉터리에 안전하게 동기화 저장."""
    saved_any = False
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    for d in _get_target_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            target = d / _LEDGER_FILE_NAME
            tmp = d / f"{_LEDGER_FILE_NAME}.tmp"
            tmp.write_text(serialized, encoding="utf-8")
            if target.exists():
                try:
                    os.replace(tmp, target)
                except OSError:
                    target.write_text(serialized, encoding="utf-8")
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
            else:
                os.replace(tmp, target)
            saved_any = True
        except Exception:
            continue
    return saved_any


def detect_recent_modified_files() -> List[str]:
    """git status 등을 통해 최근 워크스페이스에서 수정/추가된 파일 목록 감지."""
    modified_files: List[str] = []
    try:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False
        )
        if r.returncode == 0 and r.stdout:
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    modified_files.append(parts[1].replace("\\", "/"))
    except Exception:
        pass
    return modified_files[:20]


def record_evolution_event(
    reason: str,
    session_id: Optional[str] = None,
    files_modified: Optional[List[str]] = None,
    summary: Optional[str] = None,
    rebuild: bool = False,
    checkpoint_ref: Optional[str] = None
) -> Dict[str, Any]:
    """자가 수리 / 수정 / 재기동 이벤트를 원장에 등록."""
    with _lock:
        data = _load_ledger_data()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if not files_modified:
            files_modified = detect_recent_modified_files()

        event = {
            "timestamp": now_str,
            "session_id": session_id or "",
            "reason": str(reason or "").strip(),
            "rebuild": bool(rebuild),
            "checkpoint_ref": checkpoint_ref or "",
            "files_modified": files_modified or [],
            "summary": str(summary or "").strip(),
            "status": "pending_restart",
            "acknowledged": False
        }

        data["last_restart"] = event
        history = data.setdefault("history", [])
        history.insert(0, dict(event))
        data["history"] = history[:50]  # 최대 50개 보존

        _save_ledger_data(data)
        return event


def mark_restart_completed(success: bool = True, note: str = "") -> None:
    """재기동 사이클 완료 시 상태를 갱신."""
    with _lock:
        data = _load_ledger_data()
        last = data.get("last_restart")
        if last and isinstance(last, dict):
            last["status"] = "restarted_success" if success else "failed"
            if note:
                last["completion_note"] = note
            last["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

            if data.get("history") and isinstance(data["history"], list):
                data["history"][0] = dict(last)

            _save_ledger_data(data)


def get_last_restart() -> Optional[Dict[str, Any]]:
    """가장 최근의 재기동 기록 반환."""
    with _lock:
        data = _load_ledger_data()
        return data.get("last_restart")


def acknowledge_last_restart() -> bool:
    """프론트엔드 또는 사용자가 최근 재기동을 확인했음을 마킹."""
    with _lock:
        data = _load_ledger_data()
        last = data.get("last_restart")
        if last and isinstance(last, dict):
            last["acknowledged"] = True
            _save_ledger_data(data)
            return True
        return False


def get_handover_prompt_block(max_age_hours: float = 48.0) -> str:
    """새로 부팅된 에이전트의 시스템 프롬프트에 주입할 자가진화 기억 블록 생성.

    최근 48시간 이내에 발생한 재기동/자가수리 내역이 있으면 주입하고,
    오래된 기록이거나 없으면 빈 문자열을 반환한다.
    """
    try:
        last = get_last_restart()
        if not last or not isinstance(last, dict):
            return ""

        ts_str = last.get("timestamp")
        if ts_str:
            try:
                event_time = time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
                age_hours = (time.time() - event_time) / 3600.0
                if age_hours > max_age_hours:
                    return ""
            except Exception:
                pass

        reason = last.get("reason", "자가 수리 및 시스템 업데이트")
        sid = last.get("session_id") or "알 수 없음"
        rebuild_txt = "예 (server.exe 패키징 포함)" if last.get("rebuild") else "아니오 (동적 반영)"
        files = last.get("files_modified") or []
        files_txt = ", ".join(files) if files else "명시된 파일 없음"
        summary = last.get("summary") or "자가 수리/확장 작업 완료 후 서버 재기동"
        status = last.get("status", "완료")

        lines = [
            "[SELF-EVOLUTION & RESTART HANDOVER CONTEXT]",
            "You are the SAME DAON AI agent running immediately after a system self-repair / update restart.",
            "You MUST retain continuous memory and self-awareness of what you modified before the restart:",
            f"- Event Timestamp: {ts_str}",
            f"- Source Session ID: {sid}",
            f"- Restart Reason: {reason}",
            f"- server.exe Rebuild: {rebuild_txt}",
            f"- Modified Files: {files_txt}",
            f"- Task Summary: {summary}",
            f"- Restart Status: {status}",
            "",
            "CRITICAL CONTINUITY DIRECTIVE:",
            "1. You already know what you modified and why the server was restarted.",
            "2. NEVER ask the user what was just done or why the server restarted.",
            "3. If the user asks about the modification or continuing previous work, refer directly to this context with full confidence.",
            "4. Respond naturally in Korean, acknowledging your previous work if relevant to the user's prompt."
        ]
        return "\n".join(lines)
    except Exception:
        return ""
