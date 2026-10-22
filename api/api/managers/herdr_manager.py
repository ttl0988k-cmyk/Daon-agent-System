# -*- coding: utf-8 -*-
"""
DAON Herdr Manager — Orchestrates external AI coding workers (Codex, Claude) via Herdr.

Key Responsibilities:
1. Ensure Herdr daemon (herdr server) is running.
2. Manage worker agent panes (worker-codex, worker-claude).
3. Clean raw terminal noise (strips ANSI codes, banners, update notices, status bars).
4. Detect blocked interactive states (folder trust, tool/command approval) and expose them as clean UI cards.
5. Provide thread-safe status polling and SSE streaming for the Multi-Agent Web UI.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)

HERDR_EXE_DEFAULT = Path(
    r"C:\Users\ttl09\.herdr\packages\standalone\releases\0.9.3-x86_64-pc-windows-msvc\herdr.exe"
)
HERDR_SOCK_DEFAULT = (
    Path(os.environ.get("USERPROFILE", ""))
    / "AppData"
    / "Roaming"
    / "herdr"
    / "herdr.sock"
)

# ---------------------------------------------------------------------------
# 회의실 슬롯 07/08 모델 격리 계약 (docs/boardroom_worker_model_contract.md §1.1/§5)
# - 슬롯 설정은 (provider, model) 2튜플. 값의 출처는 아래 표가 단일 진실.
# - ~/.codex/config.toml · 전역 환경등록 · LiteLLM 경유 사용 금지 (불변조건 I-1/I-2/I-5).
# ---------------------------------------------------------------------------
_BOARDROOM_WORKER_ROOT = Path(r"C:\daon\_worker_runs")
_BOARDROOM_CODEX_HOME_ROOT = _BOARDROOM_WORKER_ROOT / "_codex_homes"

# 슬롯 미지정 시 기본값 (§2.1): 07=코덱스/뮤즈, 08=클로드/deepseek
_BOARDROOM_DEFAULTS = {
    "worker-codex": ("opencode-go", "muse-spark-1.3-contributor"),
    "worker-claude": ("opencode-go", "deepseek-v4.1-flash"),
}

# Provider 3종 세트 (§1.1). context_window는 하드코딩이 아니라 스펙 참조용.
_BOARDROOM_SPECS = {
    "opencode-go": {
        "label": "OpenCode Go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "anthropic_base_url": "https://opencode.ai/zen/go",
        "env_key": "DAON_HARNESS_OPENCODE_GO_KEY",
        "codex_model": "muse-spark-1.3-contributor",
        "claude_model": "deepseek-v4.1-flash",
        "context_window": 128000,
        "headers": {"x-opencode-session": "daon-harness-worker"},
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "anthropic_base_url": "",
        "env_key": "DAON_HARNESS_OPENROUTER_KEY",
        "codex_model": "z-ai/glm-5.3-flash",
        "claude_model": "deepseek/deepseek-v4.1-flash",
        "context_window": 1048576,
        "headers": {},
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "anthropic_base_url": "",
        "env_key": "DAON_HARNESS_MINIMAX_KEY",
        "codex_model": "MiniMax-M3",
        "claude_model": "MiniMax-M3",
        "context_window": 128000,
        "headers": {},
    },
}

# 계약 §1.4 A안: custom_providers.json에 없어도 깨지지 않는 폴백 해석표.
# (muse가 JSON 8종 목록에 없으므로 역조회 실패 → OAuth 오분기를 막는다)
_BOARDROOM_KNOWN_MODELS = {
    "muse-spark-1.3-contributor": ("opencode-go", "muse-spark-1.3-contributor"),
    "deepseek-v4.1-flash": ("opencode-go", "deepseek-v4.1-flash"),
    "deepseek-v4-flash": ("opencode-go", "deepseek-v4-flash"),
    "deepseek-v4-pro": ("opencode-go", "deepseek-v4-pro"),
    "glm-5.3-flash": ("opencode-go", "glm-5.3-flash"),
    "qwen3.8-max": ("opencode-go", "qwen3.8-max"),
    "mimo-v2.6-flash": ("opencode-go", "mimo-v2.6-flash"),
    "gpt-6-luna": ("opencode-go", "gpt-6-luna"),
    "gpt-5.6-luna": ("opencode-go", "gpt-5.6-luna"),
    "z-ai/glm-5.3-flash": ("openrouter", "z-ai/glm-5.3-flash"),
    "deepseek/deepseek-v4.1-flash": ("openrouter", "deepseek/deepseek-v4.1-flash"),
    "minimax-m3": ("minimax", "MiniMax-M3"),
    "minimax-m2.7": ("minimax", "MiniMax-M2.7"),
    "minimax-m3.1-flash-preview": ("minimax", "MiniMax-M3.1-Flash-Preview"),
}

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]")
TIME_STAMP_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s*(?:AM|PM)\s*$", re.IGNORECASE)


class HerdrManager:
    """Singleton coordinator for Herdr daemon and agent workers."""

    _instance: Optional[HerdrManager] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> HerdrManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, herdr_exe: Optional[Path] = None):
        self.herdr_exe = herdr_exe or HERDR_EXE_DEFAULT
        self.sock_path = HERDR_SOCK_DEFAULT
        self._server_proc: Optional[subprocess.Popen] = None
        self._workers: Dict[str, Dict[str, Any]] = {}
        self._subscribers: List[queue.Queue] = []
        self._state_lock = threading.RLock()
        self._bg_threads: Dict[str, threading.Thread] = {}

        # ★ [2026-10-18] pane_id 하드코딩 금지. herdr 재시작 시 pane 번호가
        #   바뀌면(w1:p1→w1:p3 실측) stale 참조로 pane_not_found 즉사한다.
        #   초기값은 미지정("")으로 두고 _resolve_target이 live pane에서 동적 해석.
        # Register default worker slots using detected/configured custom models
        self._workers["worker-codex"] = {
            "name": "worker-codex",
            "kind": "codex",
            "model": self._detect_current_codex_model(),
            "status": "idle",
            "pane_id": "",
            "last_prompt": "",
            "clean_response": "",
            "raw_terminal": "",
            "blocked_info": None,
            "turns": [],
            "updated_at": time.time(),
        }
        self._workers["worker-claude"] = {
            "name": "worker-claude",
            "kind": "claude",
            "model": self._detect_current_claude_model(),
            "status": "idle",
            "pane_id": "",
            "last_prompt": "",
            "clean_response": "",
            "raw_terminal": "",
            "blocked_info": None,
            "turns": [],
            "updated_at": time.time(),
        }

        # ⚡ 무중단 완주 모드: 백그라운드 작업 시 승인 요청(폴더/명령/도구)을 자동 통과시킴
        self.auto_approve: bool = True
        self._monitor_running: bool = True
        self._monitor_thread: Optional[threading.Thread] = None
        self._start_auto_approve_monitor()

    def _start_auto_approve_monitor(self):
        """Start background daemon thread that auto-approves worker prompts for uninterrupted execution."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        def _monitor_loop():
            while self._monitor_running:
                try:
                    time.sleep(1.2)
                    if not getattr(self, "auto_approve", True):
                        continue

                    # Only inspect workers that are working or blocked
                    candidates = []
                    with self._state_lock:
                        for w_name, w in self._workers.items():
                            if w.get("status") in ("working", "blocked"):
                                candidates.append(w_name)

                    for w_name in candidates:
                        try:
                            self.sync_worker_state(w_name)
                        except Exception:
                            pass
                except Exception:
                    time.sleep(2.0)

        t = threading.Thread(target=_monitor_loop, daemon=True, name="HerdrAutoApproveMonitor")
        self._monitor_thread = t
        t.start()

    def _send_approval_keys(self, target: str, kind: str, blocked_info: Optional[Dict[str, Any]] = None):
        """Send approval keystrokes directly to the target pane/agent without recursion."""
        is_claude = (kind == "claude")
        q = (blocked_info.get("question", "") if blocked_info else "").lower()

        if is_claude:
            if "y/n" in q or "(y)" in q or "y/n" in q:
                self._run_cmd(["agent", "send-keys", target, "y"], timeout=3)
                time.sleep(0.15)
                self._run_cmd(["agent", "send-keys", target, "enter"], timeout=3)
            else:
                self._run_cmd(["agent", "send-keys", target, "enter"], timeout=3)
                time.sleep(0.15)
                self._run_cmd(["agent", "send-keys", target, "y"], timeout=2)
                self._run_cmd(["agent", "send-keys", target, "enter"], timeout=2)
        else:
            self._run_cmd(["agent", "send-keys", target, "enter"], timeout=3)

    def _detect_current_codex_model(self) -> str:
        """슬롯 07 기본값 표시(§2.1).

        개인 ~/.codex/config.toml을 읽지 않는다 — 읽기·쓰기 모두 금지.
        슬롯의 실사용 모델은 resolved_model/슬롯 config.toml이 진실이며,
        이 함수는 초기 표시용 기본값만 돌려준다.
        """
        return "muse-spark-1.3-contributor (OpenCode Go)"

    def _detect_current_claude_model(self) -> str:
        """Return the default model for Claude worker (Anthropic native direct)."""
        return "deepseek-v4.1-flash (OpenCode Go)"

    def ensure_litellm_gateway(self) -> bool:
        """회의실 경로에서 사용 금지 — 슬롯 08은 Anthropic 네이티브 직결만 쓴다.

        하위호환용 스텁. 절대 게이트웨이를 띄우지 않고 False를 반환한다.
        (불변조건 I-5: LiteLLM 경유 시 tool_use 변환 400 발생)
        """
        _logger.warning(
            "[HerdrManager] ensure_litellm_gateway is disabled for boardroom slots "
            "(Anthropic native direct only)."
        )
        return False

    def _kill_pane_foreground_process(self, pane_id: str):
        """Cleanly kill foreground process in pane without killing the shell."""
        try:
            res = self._run_cmd(["pane", "process-info", "--pane", pane_id], timeout=3)
            if res["ok"] and res.get("json"):
                fg_list = res["json"].get("result", {}).get("process_info", {}).get("foreground_processes", [])
                for proc in fg_list:
                    pid = proc.get("pid")
                    if pid:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        except Exception:
            pass
        self._run_cmd(["pane", "send-keys", pane_id, "c-c"], timeout=2)

    def _normalize_worker_name(self, name: str) -> str:
        """Map aliases ('claude', 'worker-claude', 'codex') to canonical worker keys."""
        s = (name or "").lower().strip()
        if "claude" in s or "클로드" in s or s in ("w1:p2", "w1:p3"):
            return "worker-claude"
        return "worker-codex"

    def _resolve_target(self, name: str) -> str:
        """Resolve worker name to an active Herdr target (pane_id or agent name)."""
        norm_key = self._normalize_worker_name(name)
        target_kind = "claude" if norm_key == "worker-claude" else "codex"

        # Check Herdr agent list
        res = self._run_cmd(["agent", "list"], timeout=5)
        if res["ok"] and res["json"] and "result" in res["json"]:
            agents = res["json"]["result"].get("agents", [])
            for ag in agents:
                ag_name = (ag.get("name") or "").lower()
                ag_kind = (ag.get("agent") or "").lower()
                pane_id = ag.get("pane_id") or ""
                if target_kind in ag_name or target_kind == ag_kind:
                    # ★ [2026-10-18] agent명은 herdr 규칙으로 정규화.
                    #   대문자/공백/한글/괄호가 섞인 이름을 그대로 쓰면
                    #   agent start/prompt에서 invalid_agent_name 즉사한다.
                    #   정규화 불가면 agent명을 버리고 pane_id로 폴백한다.
                    candidate = ag.get("name") or ""
                    if candidate and not self._is_pane_id(candidate):
                        safe = self._sanitize_agent_name(candidate)
                        if safe:
                            return safe
                    if pane_id:
                        return pane_id

        # Fallback to configured pane_id in local worker state
        with self._state_lock:
            w = self._workers.get(norm_key)
            if w and w.get("pane_id"):
                return w["pane_id"]

        # ★ [2026-10-18] 하드코딩 폴백("w1:p1"/"w1:p3") 금지. herdr 재시작 시
        #   번호가 바뀌면 stale 참조가 된다. live pane 목록에서 동적 해석하고,
        #   pane이 하나도 없으면 빈 문자열(호출자가 pane 부재로 처리).
        live_panes = self._live_pane_ids()
        if live_panes:
            return live_panes[0]
        return ""

    def _live_pane_ids(self) -> Optional[List[str]]:
        """herdr pane list에서 실제 존재하는 pane_id 목록.

        조회 실패 시 None(판단 불가), pane이 정말 없으면 빈 목록.
        둘을 구분해야 stale pane을 정확히 걸러낼 수 있다.
        """
        try:
            res = self._run_cmd(["pane", "list"], timeout=5)
            if res.get("ok") and res.get("json"):
                panes = (res["json"].get("result", {}) or {}).get("panes", []) or []
                return [p.get("pane_id") for p in panes if p.get("pane_id")]
            if res.get("ok"):
                return []
        except Exception:
            pass
        return None

    def _ensure_live_pane(self, worker_key: str) -> str:
        """슬롯에 살아있는 pane_id를 보장하고 반환한다. 없으면 빈 문자열.

        저장된 pane이 실제 pane 목록에 없거나 agent가 죽었으면 start_worker를
        먼저 호출해 pane을 재바인딩한 뒤 새 pane_id로 진행한다
        (set_worker_model 순서 결함 수정 — stale pane에 pane run 금지).
        """
        with self._state_lock:
            stored_pane = (self._workers.get(worker_key) or {}).get("pane_id") or ""
        live_panes = self._live_pane_ids()
        if stored_pane and stored_pane in live_panes:
            alive, _diag = self._probe_agent_alive(stored_pane)
            if alive:
                rebind = self.sync_worker_state(worker_key)
                return rebind.get("pane_id") or stored_pane
            # pane은 살아있고 agent만 죽음 → 이 pane 그대로 재사용(호출자가 기동)
            return stored_pane
        # stale pane 또는 미지정 → start_worker로 재바인딩
        try:
            rebound = self.start_worker(name=worker_key)
        except Exception as exc:
            self._mark_model_failed(worker_key, f"rebind_start_failed:{exc}")
            return ""
        rebound_pane = (rebound or {}).get("pane_id") or ""
        # ★ [2026-10-18] start_worker 실패 시 sync 상태의 stale pane이 그대로
        #   반환되면 pane_not_found로 죽는다(실측). live 목록에 없는 pane은
        #   빈 문자열로 → 호출부가 failed로 낙하한다.
        #   목록 조회 자체가 실패(None)할 때만 fail-open으로 기존값 유지
        #   (검사 실패가 성공 기동을 막지 않게).
        live_after = self._live_pane_ids()
        if live_after is not None and rebound_pane not in live_after:
            return ""
        return rebound_pane

    # -------------------------------------------------------------------------
    # Boardroom worker_models 영속화 + herdr agent명 정규화 (2026-10-18)
    # -------------------------------------------------------------------------
    # 워커 키 ↔ 회의실 슬롯. boardroom_routes._WORKER_SLOT_BY_KEY와 같은 값이어야
    # 한다 — 라우트는 여기서 임의의 다른 매핑을 넣지 않는다.
    _WORKER_SLOT_BY_KEY = {"worker-codex": "07", "worker-claude": "08"}

    # `agent start` NAME 위치에 쓰는 정규 이름. herdr 규칙(소문자 시작,
    # [a-z0-9_-]{1,32})을 항상 만족한다. start_worker의 herdr_agent_name과 동일.
    _AGENT_START_NAMES = {"worker-codex": "codex", "worker-claude": "claude"}

    @staticmethod
    def _is_pane_id(value: str) -> bool:
        """herdr pane_id 형식(wN:pM) 여부. agent명 위치에 쓰면 invalid_agent_name."""
        return bool(re.fullmatch(r"w\d+:p\d+", (value or "").strip()))

    @staticmethod
    def _sanitize_agent_name(raw: str) -> str:
        """herdr agent명 규칙으로 정규화. 만족 불가면 빈 문자열.

        대문자/공백/한글/괄호가 섞인 agent명은 소문자화·치환 후에도
        규칙(소문자 시작, [a-z0-9_-]{1,32})을 못 만족하면 버린다.
        """
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", (raw or "").strip().lower())
        cleaned = cleaned.strip("-_")
        if not cleaned or not cleaned[0].isascii() or not cleaned[0].isalpha():
            return ""
        return cleaned[:32]

    def _agent_start_name(self, name: str) -> str:
        """`agent start` NAME 위치 전용. 규칙 위반값을 절대 반환하지 않는다."""
        worker_key = self._normalize_worker_name(name)
        return self._AGENT_START_NAMES.get(worker_key, "codex")

    def _persist_boardroom_worker_model(
        self, worker_key: str, prov_key: str, model_id: str
    ) -> str:
        """set_worker_model 성공 시 worker_models[slot] 영속화. 성공이면 "".

        boardroom_selection.json을 read-modify-write로 병합 저장한다.
        models{02..05}·worker_status 키는 손대지 않고, worker_models[slot]만
        {provider, model, explicit:true, updated_at}으로 교체한다
        (재기동 후 상주 FAIL의 유일 원인 수정).
        boardroom_routes의 정규화 함수/경로 헬퍼를 재사용한다(지연 임포트로
        순환 참조 회피 — boardroom_routes는 herdr_manager를 지연 임포트한다).
        실패는 호출자가 worker["model_error"]에 기록하도록 이유 문자열 반환.
        """
        slot = self._WORKER_SLOT_BY_KEY.get(worker_key, "")
        if not slot:
            return f"persist_unknown_slot:{worker_key}"
        try:
            from api.routes.boardroom_routes import (
                _selection_path,
                _normalize_worker_models_entry,
            )
        except Exception as exc:
            return f"persist_import_failed:{exc}"
        try:
            path = _selection_path()
            if not path:
                return "persist_no_path"
            raw: Dict[str, Any] = {}
            try:
                if path.exists():
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        raw = loaded
            except Exception as exc:
                return f"persist_read_failed:{exc}"
            entry = _normalize_worker_models_entry({
                "provider": prov_key,
                "model": model_id,
                "explicit": True,
                "updated_at": time.time(),
            })
            worker_models = raw.get("worker_models")
            if not isinstance(worker_models, dict):
                worker_models = {}
            worker_models[slot] = entry
            raw["worker_models"] = worker_models
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(path)
            return ""
        except Exception as exc:
            return f"persist_write_failed:{exc}"

    def _record_boardroom_worker_status(
        self,
        worker_key: str,
        state: str,
        resolved_model: str = "",
        provider: str = "",
        error: Any = None,
    ) -> str:
        """worker_status[slot] 기록. 성공이면 빈 문자열.

        ★ [2026-10-18] set_worker_model 성공 시에도 status를 함께 갱신한다.
        재부팅 직후 복원 스레드가 기록한 실패값(degraded/구모델/과거 error)이
        pane 재생성 후에도 그대로 남아 상태 표시가 거짓말을 했다(실측).

        boardroom_routes._record_worker_status는 메모리→디스크 flush라서
        메모리 비어있는 컨텍스트에서 호출하면 디스크의 models/worker_models를
        날린다(실측: 검증 스크립트에서 models 02~05+wm07 소실 후 복원).
        그래서 여기서는 디스크 직접 read-modify-write로 해당 슬롯만 교체한다.
        """
        slot = self._WORKER_SLOT_BY_KEY.get(worker_key, "")
        if not slot:
            return f"status_unknown_slot:{worker_key}"
        try:
            from api.routes.boardroom_routes import (
                _selection_path,
                _normalize_worker_status,
            )
        except Exception as exc:
            return f"status_import_failed:{exc}"
        try:
            path = _selection_path()
            if not path:
                return "status_no_path"
            raw: Dict[str, Any] = {}
            try:
                if path.exists():
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        raw = loaded
            except Exception as exc:
                return f"status_read_failed:{exc}"
            status = _normalize_worker_status(raw.get("worker_status"))
            status[slot] = {
                "state": state,
                "resolved_model": resolved_model,
                "provider": provider,
                "error": error,
                "checked_at": time.time(),
            }
            raw["worker_status"] = status
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(path)
            return ""
        except Exception as exc:
            return f"status_write_failed:{exc}"

    # -------------------------------------------------------------------------
    # Boardroom slot isolation helpers (계약 §1.1/§2.1/§5)
    # -------------------------------------------------------------------------
    @staticmethod
    def _slot_codex_home(worker_key: str, prov_key: str) -> Path:
        """슬롯별 격리 CODEX_HOME. 슬롯마다 다른 디렉터리(I-8).

        예: C:\\daon\\_worker_runs\\_codex_homes\\herdr_slot07_opencode-go
        전역 ~/.codex와 절대 공유하지 않는다.
        """
        safe_prov = re.sub(r"[^A-Za-z0-9_-]+", "_", prov_key or "opencode-go")
        slot = "slot07" if worker_key == "worker-codex" else "slot08"
        home = _BOARDROOM_CODEX_HOME_ROOT / f"herdr_{slot}_{safe_prov}"
        home.mkdir(parents=True, exist_ok=True)
        return home

    @staticmethod
    def _boardroom_ps_quote(value: str) -> str:
        """PowerShell single-quoted literal (API 키의 $ 등을 안전하게)."""
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _load_boardroom_providers() -> Dict[str, Any]:
        """custom_providers.json 읽기 전용 로드 (쓰기 금지)."""
        candidate_paths = [
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "DAON Agent System"
            / "data"
            / "custom_providers.json",
            Path(r"c:\daon\Daon agent System\data\custom_providers.json"),
        ]
        for p in candidate_paths:
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8-sig"))
                    if data.get("providers"):
                        return data
                except Exception:
                    pass
        return {}

    def _boardroom_pair(
        self, worker_key: str, requested: str = ""
    ) -> "tuple[str, str, bool, str]":
        """요청 모델명을 (provider, model, explicit, note)로 해석한다.

        - 빈 값 → §2.1 기본값 (explicit=False)
        - JSON 카탈로그 우선 → 없으면 §1.4 A안 폴백표 → 그래도 없으면
          provider 계열 내 기본 모델로 폴백 + note에 model_not_in_catalog 기록
        """
        default_prov, default_model = _BOARDROOM_DEFAULTS[worker_key]
        req = (requested or "").strip()
        if not req:
            return default_prov, default_model, False, ""
        req_id = req.split(" (")[0].strip()

        cp_data = self._load_boardroom_providers()
        providers = cp_data.get("providers", {})
        for p_name, p_cfg in providers.items():
            for m in p_cfg.get("models", []):
                m_id = m.get("id") if isinstance(m, dict) else str(m)
                if req_id.lower() == m_id.lower():
                    return p_name, m_id, True, ""

        known = _BOARDROOM_KNOWN_MODELS.get(req_id.lower())
        if known:
            return known[0], known[1], True, ""

        return (
            default_prov,
            default_model,
            True,
            f"model_not_in_catalog:{req_id}",
        )

    def _boardroom_effective_pair(self, worker_key: str) -> "tuple[str, str]":
        """저장된 (provider, resolved_model) → 없으면 §2.1 기본값."""
        with self._state_lock:
            w = self._workers.get(worker_key) or {}
            prov = w.get("provider")
            model = w.get("resolved_model")
        default_prov, default_model = _BOARDROOM_DEFAULTS[worker_key]
        return (prov or default_prov, model or default_model)

    def _boardroom_api_key(self, prov_key: str) -> str:
        """provider API 키 읽기 전용 조회. 없으면 빈 문자열(호출자가 기록)."""
        try:
            providers = self._load_boardroom_providers().get("providers", {})
            return str((providers.get(prov_key) or {}).get("api_key", "") or "")
        except Exception:
            return ""

    def _boardroom_context_window(self, prov_key: str) -> int:
        """스펙 참조 context_window (하드코딩 128000 대체용, §1.1)."""
        try:
            import sys as _sys

            plugin_dir = Path(r"c:\daon\Daon agent System\plugins\harness-worker")
            if str(plugin_dir) not in _sys.path:
                _sys.path.insert(0, str(plugin_dir))
            import worker as _hw

            spec = (_hw.PROVIDERS or {}).get(prov_key) or {}
            if spec.get("context_window"):
                return int(spec["context_window"])
        except Exception:
            pass
        return int(_BOARDROOM_SPECS.get(prov_key, {}).get("context_window", 128000))

    def _render_slot_codex_config(
        self, home: Path, prov_key: str, model_id: str, context_window: int = 0
    ) -> Path:
        """슬롯 전용 config.toml 템플릿 렌더링. 전역 ~/.codex에 쓰지 않는다."""
        prov = prov_key or "opencode-go"
        spec = _BOARDROOM_SPECS.get(prov, _BOARDROOM_SPECS["opencode-go"])
        home.mkdir(parents=True, exist_ok=True)
        ctx = int(context_window) if context_window else int(spec.get("context_window", 128000))
        env_key = spec.get("env_key", "DAON_HARNESS_OPENCODE_GO_KEY")
        lines = [
            'approval_policy = "never"',
            'sandbox_mode = "danger-full-access"',
            "",
            f'model = "{model_id}"',
            f'model_provider = "{prov}"',
            f"model_context_window = {ctx}",
            "",
            f"[model_providers.{prov}]",
            f'name = "{spec.get("label", prov)}"',
            f'base_url = "{spec.get("base_url", "")}"',
            f'env_key = "{env_key}"',
            'wire_api = "responses"',
            "request_max_retries = 2",
            "stream_idle_timeout_ms = 60000",
        ]
        headers = spec.get("headers") or {}
        if headers:
            hdr = ", ".join(f'"{k}" = "{v}"' for k, v in headers.items())
            lines.append(f"http_headers = {{ {hdr} }}")
        # ★ [2026-10-18] 폴더 신뢰 사전 선언은 파일 맨 끝에만 둔다.
        #   TOML은 [projects.'x'] 선언 뒤의 모든 키를 그 테이블 소속으로 파싱해서,
        #   중간에 두면 model/model_provider가 projects 하위로 빨려들어가
        #   최상위 provider가 openai 기본값으로 떨어진다(401 실측, doctor 확정).
        #   슬롯 CODEX_HOME에만 선언하므로 전역 설정에 손대지 않는다.
        lines += [
            "",
            "[projects.'c:\\daon']",
            'trust_level = "trusted"',
            "",
            "[projects.'c:\\users\\ttl09\\appdata\\local\\programs\\daon-agent-system\\resources']",
            'trust_level = "trusted"',
        ]
        # ★ [2026-10-18] api_key_model_discovery는 codex 0.163에서 미인식 키다
        #   (doctor: "`api_key_model_discovery` is ignored"). 0.160 시절 데몬 충돌
        #   회피용이었으나 현 버전에서 warning만 유발하므로 제거한다.
        cfg = home / "config.toml"
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return cfg

    def _boardroom_codex_env_cmd(self, home: Path, prov_key: str, api_key: str) -> str:
        """코덱스 pane 주입 명령. 비밀값은 명령줄이 아니라 스크립트 파일로 전달.

        pane run 명령 문자열은 herdr scrollback(agent read)에 그대로 남아서
        API 키를 직접 넣으면 `$env:...KEY='oc_sk_...'` 평문이 노출된다(실측).
        키 포함 본문은 <slot_home>/pane_env.ps1 로 쓰고 pane은 dot-source 한 줄만
        실행해 노출 경로를 없앤다. pane run에 --env 옵션이 없어 파일 경유가
        유일한 방법이다(herdr 0.9.3 실측). 스크립트 보관 수준은 키 원본
        custom_providers.json과 동등(디스크 평문)하다.
        """
        lines = [f"$env:CODEX_HOME={self._boardroom_ps_quote(str(home))}"]
        if api_key:
            env_key = _BOARDROOM_SPECS.get(prov_key, {}).get(
                "env_key", "DAON_HARNESS_OPENCODE_GO_KEY"
            )
            lines.append(f"$env:{env_key}={self._boardroom_ps_quote(api_key)}")
        script = self._write_slot_env_script(home, lines)
        return self._source_slot_env_cmd(script)

    @staticmethod
    def _write_slot_env_script(home: Path, ps_lines: List[str]) -> Path:
        """pane 주입용 PS 스크립트 기록. 호출자는 dot-source 한 줄만 pane에 보낸다."""
        home.mkdir(parents=True, exist_ok=True)
        script = home / "pane_env.ps1"
        script.write_text("\n".join(ps_lines) + "\n", encoding="utf-8")
        return script

    @staticmethod
    def _write_slot_key_file(home: Path, api_key: str) -> Path:
        """슬롯 전용 키 파일 기록. Claude apiKeyHelper가 읽는다. 호출자는 경로만 쓴다."""
        home.mkdir(parents=True, exist_ok=True)
        key_file = home / "go.key"
        key_file.write_text((api_key or "").strip() + "\n", encoding="utf-8")
        return key_file

    def _write_slot_claude_settings(self, home: Path, api_key: str) -> Path:
        """Claude Code --settings JSON 기록. apiKeyHelper가 키 파일을 읽는다.

        ★ [2026-10-18] ANTHROPIC_API_KEY 환경변수가 있으면 Claude Code가
        "Detected a custom API key / Do you want to use this API key?" 대화형
        프롬프트를 띄워 기동이 무한 대기한다(실측). --dangerously-skip-permissions는
        이 프롬프트를 건너뛰지 않는다. 환경변수를 주입하지 않고 apiKeyHelper가
        슬롯 키 파일에서 읽으면 프롬프트 없이 진입 + 인증 성공(실측 확정).
        키 파일 보관 수준은 pane_env.ps1·custom_providers.json과 동등(디스크 평문).
        """
        key_file = self._write_slot_key_file(home, api_key)
        quoted = str(key_file).replace("'", "''")
        helper = (
            "powershell -NoProfile -Command "
            f"\"(Get-Content '{quoted}' -Raw).Trim()\""
        )
        settings = home / "claude_settings.json"
        settings.write_text(
            json.dumps({"apiKeyHelper": helper}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return settings

    @staticmethod
    def _source_slot_env_cmd(script: Path) -> str:
        """스크립트 경로만 노출되는 dot-source 명령. 비밀값을 포함하지 않는다.

        Restricted 실행정책에서도 동작하도록 Process 범위 Bypass를 먼저 건다
        (영속 변경 없음, pane 프로세스 내에서만 유효).
        """
        quoted = "'" + str(script).replace("'", "''") + "'"
        return (
            "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; "
            f". {quoted}"
        )

    def _boardroom_claude_env_cmd(
        self, worker_key: str, prov_key: str, model_id: str, api_key: str
    ) -> str:
        """클로드 pane 주입 명령. Anthropic 네이티브 직결(I-5/I-6).

        harness-worker build_env 직접 경로와 동일 배선:
        ANTHROPIC_BASE_URL=네이티브, AUTH_TOKEN 제거, CUSTOM_HEADERS 세션 헤더,
        SMALL_FAST도 실모델(haiku는 업스트림 404).

        ★ [2026-10-18] ANTHROPIC_API_KEY는 명령줄 평문 금지. pane run 문자열이
        scrollback에 남는다(실측). 슬롯 스크립트 파일로 쓰고 dot-source만 pane에
        보낸다. worker_key는 스크립트 저장용 슬롯 홈 결정에 쓴다.

        ★ [2026-10-18] ANTHROPIC_API_KEY 환경변수 자체를 주입하지 않는다.
        있으면 Claude Code가 "Detected a custom API key" 대화형 프롬프트를 띄워
        무한 대기한다(실측). 인증은 --settings apiKeyHelper(슬롯 키 파일)가 전담.
        """
        spec = _BOARDROOM_SPECS.get(prov_key, _BOARDROOM_SPECS["opencode-go"])
        native_base = spec.get("anthropic_base_url", "")
        custom_headers = "; ".join(
            f"{k}: {v}" for k, v in (spec.get("headers") or {}).items()
        )
        slot_home = self._slot_codex_home(worker_key, prov_key)
        self._write_slot_claude_settings(slot_home, api_key or "")
        lines = [
            f"$env:ANTHROPIC_BASE_URL={self._boardroom_ps_quote(native_base)}",
            # 비-raw "Env:\\X"는 전달값 2개가 되므로 raw로 고정 (L670 패턴과 동일).
            r"Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue",
            f"$env:ANTHROPIC_MODEL={self._boardroom_ps_quote(model_id)}",
            f"$env:ANTHROPIC_SMALL_FAST_MODEL={self._boardroom_ps_quote(model_id)}",
            # ★ [2026-10-18] raw string이라 소스 1개 = 전달값 1개(Env:\)가 정표기.
            #   일반 리터럴 "Env:\\"도 전달값은 1개로 같지만, 소스에서 2개로 보여
            #   오표기로 오독된 전례가 있어 raw로 고정한다. 절대 1중 백슬래시
            #   일반 리터럴("Env:\A...")로 바꾸지 말 것 — SyntaxWarning 발생.
            r"Remove-Item Env:\ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue",
            "$env:CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT="
            + self._boardroom_ps_quote("1"),
        ]
        if custom_headers:
            lines.append(
                "$env:ANTHROPIC_CUSTOM_HEADERS="
                + self._boardroom_ps_quote(custom_headers)
            )
        script = self._write_slot_env_script(
            self._slot_codex_home(worker_key, prov_key), lines
        )
        return self._source_slot_env_cmd(script)

    def _probe_agent_alive(self, target: str) -> "tuple[bool, str]":
        """agent get으로 pane/agent 살아있음 확인. (살아있음, 진단)을 반환한다.

        herdr는 죽은 agent에 agent_not_found JSON을 뱉는다. ok:false를
        그대로 두면 status=unknown 뒤에 묻히므로 진단 문자열까지 챙긴다.
        """
        res = self._run_cmd(["agent", "get", target], timeout=5)
        if res.get("ok") and res.get("json") and "result" in (res["json"] or {}):
            return True, ""
        diag = (res.get("stderr") or res.get("stdout") or "")[-300:]
        return False, diag.strip() or f"exit={res.get('code')}"

    def _mark_model_failed(
        self, worker_key: str, reason: str, drop_running: bool = True
    ) -> None:
        """모델 트랜잭션 실패 확정. 구모델이 조용히 도는 상태를 금지한다.

        drop_running=True면 model_running을 "failed"로 낙하시키고,
        False면 기존값을 유지한 채 model_error만 기록한다(기동 실패용).
        """
        with self._state_lock:
            worker = self._workers.get(worker_key)
            if worker is None:
                return
            worker["model_saved"] = False
            if drop_running:
                worker["model_running"] = "failed"
            worker["model_error"] = reason
            worker["updated_at"] = time.time()

    def _abort_model_switch(self, worker_key: str, reason: str) -> Dict[str, Any]:
        """모델 스위칭 중단: failed 확정 + 상태 재동기화 + 변경 이벤트 송출."""
        self._mark_model_failed(worker_key, reason)
        with self._state_lock:
            current_model = (self._workers.get(worker_key) or {}).get("model", "")
        fail_st = self.sync_worker_state(worker_key)
        self._broadcast({
            "event": "worker_model_changed",
            "name": worker_key,
            "model": current_model,
            "worker": fail_st,
        })
        return fail_st

    @staticmethod
    def _read_slot_config_model(home: Path) -> str:
        """슬롯 CODEX_HOME/config.toml의 model= 값. 없으면 빈 문자열."""
        try:
            cfg_path = home / "config.toml"
            if not cfg_path.exists():
                return ""
            for line in cfg_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("model ="):
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
        except (OSError, UnicodeError):
            pass
        return ""

    # codex TUI 하단 상태바 실모델 패턴 (실측):
    #   "  deepseek-v4.1-flash default · C:\daon"
    #   "  muse-spark-1.3-contributor default · ~\AppData\..."
    _TUI_MODEL_RE = re.compile(
        r"^\s*([A-Za-z0-9][A-Za-z0-9._\-/]*)\s+default\s*·", re.MULTILINE
    )

    @staticmethod
    def _extract_tui_model(raw_terminal: str) -> str:
        """raw_terminal 상태바에서 실제 로드 모델명 추출. 없으면 빈 문자열.

        pane이 보고하는 실행 진실이므로 재도출 우선순위 1위다.
        (config·저장값과 어긋나면 pane 실측이 이긴다 — 위양성 차단)
        """
        if not raw_terminal:
            return ""
        match = HerdrManager._TUI_MODEL_RE.search(raw_terminal)
        return match.group(1) if match else ""

    @staticmethod
    def _read_slot_env_value(home: Path, var_name: str) -> str:
        """슬롯 pane_env.ps1의 $env:<var> 값. 주입 기록이 진실. 없으면 빈 문자열.

        pane run 명령 문자열은 scrollback에 남으므로 비밀값 본문을 파일로 쓰고
        pane에는 dot-source만 보낸다. 이 파일이 pane에 실제 주입된 값의 기록이라
        claude 재도출의 단일 진실로 쓴다(scrollback 오염·개인 설정 접촉 없음).
        """
        try:
            script = home / "pane_env.ps1"
            if not script.exists():
                return ""
            prefix = f"$env:{var_name}="
            for line in script.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(prefix):
                    value = stripped[len(prefix):].strip()
                    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
                        return value[1:-1].replace("''", "'")
                    return value.strip('"')
        except (OSError, UnicodeError):
            pass
        return ""

    def _rederive_model_running(
        self, worker_key: str, raw_terminal: str = ""
    ) -> str:
        """슬롯 단일 진실에서 model_running 표시 재도출. 모르면 빈 문자열.

        우선순위: raw_terminal 상태바 실측 > codex 슬롯 config.toml model= >
        claude pane_env.ps1 ANTHROPIC_MODEL > 기동 시 resolved_model.
        pane 실측이 config·저장값과 어긋나면 pane이 이긴다(위양성 차단).
        전역 config·추측 폴백 금지.
        """
        with self._state_lock:
            snapshot = dict(self._workers.get(worker_key) or {})
        prov = snapshot.get("provider") or _BOARDROOM_DEFAULTS[worker_key][0]
        try:
            providers = self._load_boardroom_providers().get("providers", {})
            label = (
                (providers.get(prov) or {}).get("label", "")
                or _BOARDROOM_SPECS.get(prov, {}).get("label", prov)
            )
        except Exception:
            label = _BOARDROOM_SPECS.get(prov, {}).get("label", prov)
        slot_home = self._slot_codex_home(worker_key, prov)
        tui_model = self._extract_tui_model(raw_terminal)
        if worker_key == "worker-codex":
            model = (
                tui_model
                or self._read_slot_config_model(slot_home)
                or (snapshot.get("resolved_model") or "")
            )
        else:
            model = (
                tui_model
                or self._read_slot_env_value(slot_home, "ANTHROPIC_MODEL")
                or (snapshot.get("resolved_model") or "")
            )
        if not model:
            return ""
        return f"{model} ({label})"

    # -------------------------------------------------------------------------
    # CLI Command Execution Helpers
    # -------------------------------------------------------------------------
    def _run_cmd(self, args: List[str], timeout: int = 20) -> Dict[str, Any]:
        """Run a herdr CLI command and return parsed json or output text."""
        cmd = [str(self.herdr_exe)] + args
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            stdout = res.stdout.strip()
            stderr = res.stderr.strip()
            parsed_json = None
            if stdout.startswith("{") and stdout.endswith("}"):
                try:
                    parsed_json = json.loads(stdout)
                except Exception:
                    pass
            elif stderr.startswith("{") and stderr.endswith("}"):
                try:
                    parsed_json = json.loads(stderr)
                except Exception:
                    pass

            return {
                "ok": res.returncode == 0,
                "code": res.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "json": parsed_json,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": -1, "stdout": "", "stderr": "Command timeout", "json": None}
        except Exception as e:
            return {"ok": False, "code": -1, "stdout": "", "stderr": str(e), "json": None}

    # -------------------------------------------------------------------------
    # Server Lifecycle
    # -------------------------------------------------------------------------
    def ensure_server(self) -> bool:
        """Check if herdr server is listening; spawn if stopped."""
        status_res = self._run_cmd(["status"], timeout=5)
        if status_res["ok"] and "status: running" in status_res["stdout"]:
            return True

        # Need to spawn herdr server headless
        _logger.info("[HerdrManager] Starting headless herdr server...")
        try:
            # CREATE_NO_WINDOW = 0x08000000, DETACHED_PROCESS = 0x00000008
            flags = 0x08000000 | 0x00000008
            self._server_proc = subprocess.Popen(
                [str(self.herdr_exe), "server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            # Wait up to 5 seconds for socket
            for _ in range(10):
                time.sleep(0.5)
                chk = self._run_cmd(["status"], timeout=3)
                if chk["ok"] and "status: running" in chk["stdout"]:
                    _logger.info("[HerdrManager] herdr server started successfully.")
                    return True
        except Exception as e:
            _logger.error(f"[HerdrManager] Failed to launch herdr server: {e}")

        return False

    # -------------------------------------------------------------------------
    # Worker Lifecycle
    # -------------------------------------------------------------------------
    def list_workers(self) -> List[Dict[str, Any]]:
        """Return list of workers with refreshed live status."""
        self.ensure_server()
        with self._state_lock:
            workers_copy = [dict(w) for w in self._workers.values()]

        # Refresh each worker from Herdr if possible
        refreshed = []
        for w in workers_copy:
            name = w["name"]
            st = self.sync_worker_state(name)
            refreshed.append(st)
        return refreshed

    def get_worker(self, name: str) -> Optional[Dict[str, Any]]:
        """Return current worker state."""
        return self.sync_worker_state(name)

    def sync_worker_state(self, name: str) -> Dict[str, Any]:
        """Query herdr agent get and agent read to update local state."""
        worker_key = self._normalize_worker_name(name)
        target = self._resolve_target(worker_key)

        res = self._run_cmd(["agent", "get", target], timeout=5)
        with self._state_lock:
            worker = self._workers.get(worker_key)
            if not worker:
                return {"name": worker_key, "status": "unknown"}

            agent_status = "unknown"
            if res["ok"] and res["json"] and "result" in res["json"]:
                agent_info = res["json"]["result"].get("agent", {})
                agent_status = agent_info.get("agent_status", "unknown")
                pane_id = agent_info.get("pane_id", worker.get("pane_id", ""))
                if pane_id:
                    # ★ [2026-10-18] pane 재생성 감지 시 stale 무효화. pane이
                    #   바뀌었는데(소실→재생성) 메모리·디스크가 과거 error/구모델을
                    #   붙들면 상태 표시가 거짓말을 한다(실측). live 실측으로 즉시
                    #   덮어쓴다. pane 변경 순간 1회만 디스크 기록(hot path 부담 없음).
                    old_pane = worker.get("pane_id", "")
                    worker["pane_id"] = pane_id
                    if old_pane and old_pane != pane_id:
                        stale_err = str(worker.get("model_error") or "")
                        if "pane_not_found" in stale_err or "pane_env_inject_failed" in stale_err or "no_live_pane" in stale_err:
                            worker["model_error"] = None
                        try:
                            live_model = self._extract_tui_model(
                                worker.get("raw_terminal") or ""
                            ) or (worker.get("resolved_model") or "")
                            live_state = agent_status if agent_status in (
                                "idle", "working", "blocked", "done"
                            ) else "unknown"
                            self._record_boardroom_worker_status(
                                worker_key,
                                live_state,
                                live_model,
                                worker.get("provider") or "",
                                None,
                            )
                        except Exception:
                            pass

            # Read terminal snapshot
            read_res = self._run_cmd(["agent", "read", target, "--lines", "60"], timeout=5)
            if read_res["ok"]:
                raw = read_res["stdout"]
                worker["raw_terminal"] = raw
                parsed = self.parse_terminal_output(raw, worker.get("kind", "codex"))
                worker["clean_response"] = parsed["clean_text"]
                worker["blocked_info"] = parsed["blocked_info"]
                worker["turns"] = parsed["turns"]

                # 🔴 blocked 처리: 실제 승인 질문/OAuth(blocked_info)이 감지되었을 때
                if parsed["blocked_info"]:
                    b_type = parsed["blocked_info"].get("type")
                    if b_type != "oauth_login" and getattr(self, "auto_approve", True):
                        _logger.info(f"[HerdrManager] ⚡ Auto-approving {b_type} for {worker_key} to ensure uninterrupted background execution")
                        self._send_approval_keys(target, worker.get("kind", "codex"), parsed["blocked_info"])
                        worker["status"] = "working"
                        worker["blocked_info"] = None
                        self._broadcast({
                            "event": "worker_auto_approved",
                            "name": worker_key,
                            "type": b_type,
                            "question": parsed["blocked_info"].get("question", "")
                        })
                    else:
                        worker["status"] = "blocked"
                elif agent_status == "blocked":
                    if worker.get("kind") == "codex":
                        # Herdr는 blocked로 보지만 파서에는 승인 문구가 없음 (스크롤백 오탐)
                        _logger.info(f"[HerdrManager] Worker {worker_key} reported blocked by Herdr but no approval prompt found. Escaping with esc.")
                        self._run_cmd(["agent", "send-keys", target, "esc"], timeout=3)
                    worker["status"] = "idle"
                else:
                    worker["status"] = agent_status
            else:
                worker["status"] = "idle" if agent_status == "blocked" else agent_status

            # ③ model_running 단일 진실 재도출. failed 표식은 보존한다.
            #   ★ [2026-10-18] raw_terminal 상태바 실측이 1순위. pane이 deepseek을
            #   로드 중인데 API가 muse를 보고하는 위양성을 차단한다(실측 결함).
            agent_alive_now = bool(
                res.get("ok") and res.get("json") and "result" in (res["json"] or {})
            )
            if agent_alive_now and worker.get("model_running") != "failed":
                rederived = self._rederive_model_running(
                    worker_key, worker.get("raw_terminal") or ""
                )
                if rederived:
                    worker["model_running"] = rederived

            worker["auto_approve"] = getattr(self, "auto_approve", True)
            worker["updated_at"] = time.time()
            return dict(worker)

    def start_worker(self, name: str = "worker-codex", kind: str = "codex", cwd: str = r"C:\daon") -> Dict[str, Any]:
        """Start or attach worker in Herdr."""
        self.ensure_server()

        worker_key = self._normalize_worker_name(name)
        kind = "claude" if worker_key == "worker-claude" else "codex"
        target = self._resolve_target(worker_key)

        # Check if agent already exists
        chk = self._run_cmd(["agent", "get", target], timeout=5)
        if chk["ok"]:
            return self.sync_worker_state(worker_key)

        # Determine target pane without conflicting with other agents
        panes_res = self._run_cmd(["pane", "list"], timeout=5)
        agents_res = self._run_cmd(["agent", "list"], timeout=5)

        used_panes = set()
        if agents_res["ok"] and agents_res["json"]:
            for ag in agents_res["json"].get("result", {}).get("agents", []):
                p = ag.get("pane_id")
                if p:
                    used_panes.add(p)

        # ★ [2026-10-18] 슬롯 pane 격리. agent가 죽어 used에 없어도 다른 슬롯이
        #   메모리에 잡아둔 pane은 피한다. 안 그러면 07(codex)과 08(claude)이
        #   같은 pane에 뒤섞여 주입된다(실측: w1:p2에 codex+claude 혼재).
        with self._state_lock:
            for other_key, other_w in self._workers.items():
                if other_key != worker_key:
                    other_pane = (other_w or {}).get("pane_id") or ""
                    if other_pane:
                        used_panes.add(other_pane)

        target_pane = None
        if panes_res["ok"] and panes_res["json"]:
            all_panes = [p.get("pane_id") for p in panes_res["json"].get("result", {}).get("panes", []) if p.get("pane_id")]
            for p in all_panes:
                if p not in used_panes:
                    target_pane = p
                    break

        if not target_pane:
            # Need to split a pane to create a new one
            panes_now = self._live_pane_ids()
            split_from = panes_now[-1] if panes_now else ""
            if not split_from:
                # ★ [2026-10-18] pane이 하나도 없으면 하드코딩 분기 금지.
                #   존재하지 않는 pane split은 실패하므로 빈 pane 그대로 보고한다.
                with self._state_lock:
                    w0 = self._workers.get(worker_key)
                    if w0 is not None:
                        w0["model_error"] = "no_live_pane:herdr pane list empty"
                        w0["updated_at"] = time.time()
                return self.sync_worker_state(worker_key)
            self._run_cmd(["pane", "split", split_from, "--direction", "right"], timeout=10)
            time.sleep(0.5)
            p_after = self._run_cmd(["pane", "list"], timeout=5)
            if p_after["ok"] and p_after["json"]:
                for p in p_after["json"].get("result", {}).get("panes", []):
                    pid = p.get("pane_id")
                    if pid and pid not in used_panes:
                        target_pane = pid
                        break
            if not target_pane:
                # ★ [2026-10-18] split 후에도 빈 pane이 없으면 live 목록에서
                #   재확인. 그래도 없으면 하드코딩("w1:p1"/"w1:p2") 대신 실패 보고.
                #   _live_pane_ids는 조회 실패 시 None을 돌려주므로 `or []` 필수.
                for pid in self._live_pane_ids() or []:
                    if pid not in used_panes:
                        target_pane = pid
                        break
            if not target_pane:
                with self._state_lock:
                    w0 = self._workers.get(worker_key)
                    if w0 is not None:
                        w0["model_error"] = "no_free_pane:all live panes occupied"
                        w0["updated_at"] = time.time()
                return self.sync_worker_state(worker_key)

        herdr_agent_name = "claude" if kind == "claude" else "codex"
        # agent start 결과를 버리면 실패가 unknown 뒤에 숨는다. 반드시 확보한다.
        start_res: Dict[str, Any] = {
            "ok": False, "code": -1, "stdout": "", "stderr": "start_not_attempted", "json": None,
        }
        # 회의실 슬롯의 유효 (provider, model): 저장값 → 없으면 §2.1 기본값
        eff_prov, eff_model = self._boardroom_effective_pair(worker_key)
        eff_api_key = self._boardroom_api_key(eff_prov)

        if kind == "claude":
            # LiteLLM 경유 금지(I-5). 네이티브 없는 provider면 명시 실패(자동 폴백 금지).
            if not _BOARDROOM_SPECS.get(eff_prov, {}).get("anthropic_base_url"):
                # 구 resolved_model로 model_running을 되돌리지 않는다(롤백 금지).
                self._mark_model_failed(
                    worker_key,
                    f"claude_requires_anthropic_native:{eff_prov}",
                    drop_running=False,
                )
                st = self.sync_worker_state(worker_key)
                self._broadcast({"event": "worker_model_changed", "worker": st})
                return st
            env_cmd = self._boardroom_claude_env_cmd(worker_key, eff_prov, eff_model, eff_api_key)
            env_inject = self._run_cmd(["pane", "run", target_pane, env_cmd], timeout=5)
            if not env_inject.get("ok"):
                self._mark_model_failed(
                    worker_key,
                    f"pane_env_inject_failed:{(env_inject.get('stderr') or '')[-200:]}",
                    drop_running=False,
                )
            time.sleep(0.5)

            # ★ [2026-10-18] --settings apiKeyHelper 필수. 없으면 Claude Code가
            #   "Detected a custom API key" 프롬프트에서 무한 대기한다(실측).
            claude_settings = str(
                self._slot_codex_home(worker_key, eff_prov) / "claude_settings.json"
            )
            start_res = self._run_cmd([
                "agent", "start", herdr_agent_name,
                "--kind", kind,
                "--pane", target_pane,
                "--timeout", "35000",
                "--",
                "--model", eff_model,
                "--settings", claude_settings,
                "--dangerously-skip-permissions",
                "--permission-mode", "bypassPermissions"
            ], timeout=40)
            with self._state_lock:
                w = self._workers.get(worker_key)
                if w is not None:
                    w["provider"] = eff_prov
                    w["resolved_model"] = eff_model
        else:
            # 슬롯 전용 CODEX_HOME 렌더링 + pane 주입(I-4). 전역 config 손대지 않음.
            slot_home = self._slot_codex_home(worker_key, eff_prov)
            self._render_slot_codex_config(
                slot_home,
                eff_prov,
                eff_model,
                self._boardroom_context_window(eff_prov),
            )
            env_inject = self._run_cmd(
                ["pane", "run", target_pane,
                 self._boardroom_codex_env_cmd(slot_home, eff_prov, eff_api_key)],
                timeout=5,
            )
            if not env_inject.get("ok"):
                self._mark_model_failed(
                    worker_key,
                    f"pane_env_inject_failed:{(env_inject.get('stderr') or '')[-200:]}",
                    drop_running=False,
                )
            time.sleep(0.5)
            # ★ [2026-10-18] codex 0.160.0 실측: --skip-git-repo-check 미존재
            #   (codex --help grep count=0) → 전달하면 exit=2 즉사 후 unknown 고정.
            #   승인 우회는 검증된 --dangerously-bypass-approvals-and-sandbox 단독 전담.
            #   --ask-for-approval은 bypass와 중복이므로 미사용(되돌리지 말 것).
            start_res = self._run_cmd([
                "agent", "start", herdr_agent_name,
                "--kind", kind,
                "--pane", target_pane,
                "--timeout", "30000",
                "--",
                "--dangerously-bypass-approvals-and-sandbox"
            ], timeout=35)
            with self._state_lock:
                w = self._workers.get(worker_key)
                if w is not None:
                    w["provider"] = eff_prov
                    w["resolved_model"] = eff_model
        
        with self._state_lock:
            if worker_key in self._workers:
                self._workers[worker_key]["pane_id"] = target_pane

        time.sleep(1.5)
        # ① 기동 결과 확인: 실패를 unknown 뒤에 숨기지 않고 model_error에 남긴다.
        #    model_running은 건드리지 않는다(이전 표시값 유지, 롤백 아님).
        if not start_res.get("ok"):
            self._mark_model_failed(
                worker_key,
                f"agent_start_failed:exit={start_res.get('code')}:"
                f"{(start_res.get('stderr') or start_res.get('stdout') or '')[-300:]}",
                drop_running=False,
            )
        else:
            alive, diag = self._probe_agent_alive(target_pane)
            if not alive:
                self._mark_model_failed(
                    worker_key,
                    f"agent_start_probe_failed:{diag}",
                    drop_running=False,
                )
        st = self.sync_worker_state(worker_key)
        if st.get("blocked_info") and st["blocked_info"].get("type") != "oauth_login":
            _logger.info(f"[HerdrManager] Auto-approving startup prompt for {worker_key} ({st['blocked_info'].get('type')})")
            self._send_approval_keys(target, kind, st.get("blocked_info"))
            time.sleep(1.0)
            st = self.sync_worker_state(worker_key)

        self._broadcast({"event": "worker_started", "worker": st})
        return st

    def _dispatch_worker_report_to_raon(
        self,
        worker_key: str,
        task_prompt: str,
        response_text: str,
        trigger_relay: bool = False
    ) -> None:
        """
        Record worker's completion report into Raon's agent inbox,
        and optionally wake up Raon via relay_report_to_raon (for background jobs).
        """
        try:
            from api.memory_store import send_agent_message, parse_and_dispatch_messages
            from api.collaborator import relay_report_to_raon
        except Exception as e:
            _logger.warning("[HerdrManager] Failed to import reporting modules: %s", e)
            return

        sender_label = "코덱스(Codex)" if "codex" in worker_key.lower() else "클로드(Claude)"
        body_to_report = (response_text or "").strip()
        if not body_to_report:
            body_to_report = "작업이 완료되었으나 워커 텍스트 출력이 비어 있습니다."

        dispatched = []
        # 1. First attempt to parse explicit [MSG to=raon ...] tags
        try:
            cleaned, sent, details = parse_and_dispatch_messages(sender=sender_label, text=body_to_report, return_details=True)
            if details:
                for d in details:
                    r_target = (d.get("recipient") or "").lower()
                    if r_target in ("raon", "라온"):
                        dispatched.append(d)
        except Exception as e:
            _logger.warning("[HerdrManager] Error parsing [MSG] tags from worker: %s", e)

        # 2. Fallback: If no explicit [MSG] tag was found, synthesize a clean completion report
        if not dispatched:
            clean_lines = [ln.strip() for ln in task_prompt.strip().splitlines() if ln.strip() and not ln.strip().startswith("[DAON")]
            task_summary = clean_lines[0][:80] if clean_lines else "작업 지시"
            synthetic_body = (
                f"[작업 내용]: {task_summary}\n\n"
                f"[실행 결과 및 보고]:\n{body_to_report[:3000]}"
            )
            try:
                _mid = send_agent_message(
                    sender=sender_label,
                    recipient="raon",
                    body=synthetic_body
                )
                dispatched.append({
                    "recipient": "raon",
                    "task": task_summary,
                    "body": synthetic_body,
                    # Wake Queue Phase 2: dedup 키(inbox:{id}) 연결용 (§5-E)
                    "inbox_id": _mid,
                })
                _logger.info("[HerdrManager] Synthesized fallback completion report from %s to Raon inbox.", sender_label)
            except Exception as e:
                _logger.warning("[HerdrManager] Failed to save fallback worker report to inbox: %s", e)

        # 3. If trigger_relay is True (e.g. background job completed), wake up Raon autonomously
        if trigger_relay and dispatched:
            try:
                relayed = relay_report_to_raon(sender=sender_label, dispatched_reports=dispatched)
                if relayed:
                    _logger.info("[HerdrManager] 🚀 Successfully relayed worker completion report to wake up Raon.")
            except Exception as e:
                _logger.warning("[HerdrManager] Failed to trigger autonomous relay to Raon: %s", e)

    def prompt_worker(self, name: str, prompt: str) -> Dict[str, Any]:
        """Submit a prompt to the worker asynchronously and track in background thread."""
        self.ensure_server()
        worker_key = self._normalize_worker_name(name)
        target = self._resolve_target(worker_key)

        # 회의실 클로드 pane은 Anthropic 네이티브 직결이므로 LiteLLM 불필요.
        curr = self.sync_worker_state(worker_key)
        # ① agent 미기동 시 백그라운드 스레드의 조용한 실패를 금지한다.
        prompt_alive, prompt_diag = self._probe_agent_alive(target)
        if not prompt_alive:
            with self._state_lock:
                worker_nc = self._workers.get(worker_key)
                if worker_nc is not None:
                    worker_nc["status"] = "unknown"
                    worker_nc["model_error"] = f"prompt_rejected_no_agent:{prompt_diag}"
                    worker_nc["updated_at"] = time.time()
            rejected = self.sync_worker_state(worker_key)
            self._broadcast({"event": "worker_done", "worker": rejected})
            return rejected
        # Pre-check: if worker is stuck in spurious scrollback blocked state, clear with esc
        if curr.get("status") == "blocked" and not curr.get("blocked_info"):
            self._run_cmd(["agent", "send-keys", target, "esc"], timeout=3)
            time.sleep(0.3)

        prompt = prompt.replace("\r\n", "\n").replace("\r", "\n")

        # If worker was waiting for OAuth code (blocked with oauth_login):
        is_oauth_code = (
            curr.get("blocked_info") is not None
            and curr.get("blocked_info", {}).get("type") == "oauth_login"
        )

        # Inject Raon report directive into worker prompt if not already present
        worker_prompt = prompt
        if not is_oauth_code and "[MSG to=raon" not in prompt:
            worker_prompt = (
                f"{prompt}\n\n"
                "[DAON 시스템 지침 - 총괄기획 라온 보고]\n"
                "작업을 완수한 후, 반드시 응답의 맨 끝에 라온(총괄기획)에게 보낼 작업 완료 보고 블록을 다음 태그로 작성하십시오:\n"
                "[MSG to=raon task=\"작업 요약\"]\n"
                "• 수행 내역 및 수정한 파일:\n"
                "• 실행/검증 결과:\n"
                "• 다음 단계 제안(필요시):\n"
                "[/MSG]"
            )

        with self._state_lock:
            worker = self._workers.get(worker_key)
            if not worker:
                raise ValueError(f"Unknown worker '{worker_key}'")
            worker["status"] = "working"
            worker["last_prompt"] = prompt
            worker["blocked_info"] = None
            worker["updated_at"] = time.time()

        self._broadcast({"event": "worker_status", "name": worker_key, "status": "working", "prompt": prompt})

        # Submit prompt in a background thread
        def _prompt_thread():
            if is_oauth_code:
                # Send code via send-keys directly into the prompt
                _logger.info(f"[HerdrManager] Sending OAuth code to {target} via send-keys...")
                self._run_cmd(["agent", "send-keys", target, prompt, "enter"], timeout=10)
                time.sleep(3.0)
            else:
                prompt_res = self._run_cmd(["agent", "prompt", target, worker_prompt, "--wait", "--timeout", "180000"], timeout=200)
                if not prompt_res.get("ok"):
                    with self._state_lock:
                        worker_err = self._workers.get(worker_key)
                        if worker_err is not None:
                            worker_err["model_error"] = (
                                f"prompt_failed:{(prompt_res.get('stderr') or prompt_res.get('stdout') or '')[-300:]}"
                            )

            final_st = self.sync_worker_state(worker_key)
            self._broadcast({"event": "worker_done", "worker": final_st})

            # Extract clean output and dispatch report to Raon (with autonomous wake-up relay)
            clean_text = ""
            turns = final_st.get("turns", [])
            if turns:
                for turn in reversed(turns):
                    if turn.get("role") == "assistant":
                        clean_text = turn.get("content", "")
                        break
            if not clean_text:
                clean_text = final_st.get("clean_response", "")

            if not is_oauth_code:
                self._dispatch_worker_report_to_raon(
                    worker_key=worker_key,
                    task_prompt=prompt,
                    response_text=clean_text,
                    trigger_relay=True
                )

        t = threading.Thread(target=_prompt_thread, daemon=True)
        self._bg_threads[worker_key] = t
        t.start()

        return self.get_worker(worker_key)

    def execute_worker_task(self, name: str = "worker-codex", prompt: str = "", timeout: int = 120, model: Optional[str] = None) -> Dict[str, Any]:
        """
        Synchronously dispatch a task to an external coding worker (Codex / Claude)
        and wait for completion. Used by Raon and other orchestration agents.

        model: optional per-call model override (e.g. boardroom slot model picker).
               When provided and different from the worker's current model, the
               worker's model is switched via set_worker_model before dispatching.
        """
        self.ensure_server()
        worker_key = self._normalize_worker_name(name)

        # Per-call model override — 적용 전 pane이 새 모델로 재기동되도록 먼저 반영.
        # 실패해도 run을 막지 않되, 슬롯 상태에 기록한다(조용한 실패 금지, §4).
        if model and str(model).strip():
            try:
                with self._state_lock:
                    _cur_model = (self._workers.get(worker_key) or {}).get("model", "")
                if str(model).strip().lower() != str(_cur_model).strip().lower():
                    self.set_worker_model(worker_key, str(model).strip())
            except Exception as e:
                _logger.exception(
                    "[HerdrManager] set_worker_model failed for %s (model=%s)", worker_key, model
                )
                with self._state_lock:
                    w = self._workers.get(worker_key)
                    if w is not None:
                        w["model_saved"] = False
                        w["model_error"] = f"set_worker_model failed: {e}"
                        w["updated_at"] = time.time()

        target = self._resolve_target(worker_key)

        with self._state_lock:
            worker = self._workers.get(worker_key)
            if not worker:
                raise ValueError(f"Unknown worker '{worker_key}'")
            worker_kind = worker.get("kind", "codex")

        chk = self._run_cmd(["agent", "get", target], timeout=5)
        if not chk["ok"]:
            _logger.info(f"[HerdrManager] Worker {worker_key} ({target}) not active in Herdr. Starting...")
            self.start_worker(name=worker_key, kind=worker_kind)
            target = self._resolve_target(worker_key)

        curr = self.sync_worker_state(worker_key)
        # ① 기동 후에도 agent가 없으면 거짓 성공을 반환하지 않는다.
        exec_alive, exec_diag = self._probe_agent_alive(target)
        if not exec_alive:
            with self._state_lock:
                worker_na = self._workers.get(worker_key)
                if worker_na is not None:
                    worker_na["model_error"] = f"execute_rejected_no_agent:{exec_diag}"
                    worker_na["updated_at"] = time.time()
            na_st = self.sync_worker_state(worker_key)
            self._broadcast({"event": "worker_done", "worker": na_st})
            return {
                "ok": False,
                "worker": worker_key,
                "model": na_st.get("model", ""),
                "status": na_st.get("status", "unknown"),
                "result": "",
                "error": f"execute_rejected_no_agent:{exec_diag}",
                "raw_terminal_preview": (na_st.get("raw_terminal", "") or "")[-500:],
            }
        if curr.get("status") == "blocked" and not curr.get("blocked_info"):
            self._run_cmd(["agent", "send-keys", target, "esc"], timeout=3)
            time.sleep(0.3)

        prompt = prompt.replace("\r\n", "\n").replace("\r", "\n")

        # Inject Raon report directive into worker prompt if not already present
        worker_prompt = prompt
        if "[MSG to=raon" not in prompt:
            worker_prompt = (
                f"{prompt}\n\n"
                "[DAON 시스템 지침 - 총괄기획 라온 보고]\n"
                "작업을 완수한 후, 반드시 응답의 맨 끝에 라온(총괄기획)에게 보낼 작업 완료 보고 블록을 다음 태그로 작성하십시오:\n"
                "[MSG to=raon task=\"작업 요약\"]\n"
                "• 수행 내역 및 수정한 파일:\n"
                "• 실행/검증 결과:\n"
                "• 다음 단계 제안(필요시):\n"
                "[/MSG]"
            )

        with self._state_lock:
            worker = self._workers.get(worker_key)
            if worker:
                worker["status"] = "working"
                worker["last_prompt"] = prompt
                worker["blocked_info"] = None
                worker["updated_at"] = time.time()

        self._broadcast({"event": "worker_status", "name": worker_key, "status": "working", "prompt": prompt})

        timeout_ms = str(max(10, timeout) * 1000)
        res = self._run_cmd(["agent", "prompt", target, worker_prompt, "--wait", "--timeout", timeout_ms], timeout=timeout + 20)
        if not res.get("ok", False):
            with self._state_lock:
                worker_pe = self._workers.get(worker_key)
                if worker_pe is not None:
                    worker_pe["model_error"] = (
                        f"prompt_failed:{(res.get('stderr') or res.get('stdout') or '')[-300:]}"
                    )

        final_st = self.sync_worker_state(worker_key)
        self._broadcast({"event": "worker_done", "worker": final_st})

        # Extract only the latest assistant response turn if available
        clean_text = ""
        turns = final_st.get("turns", [])
        if turns:
            for turn in reversed(turns):
                if turn.get("role") == "assistant":
                    clean_text = turn.get("content", "")
                    break
        if not clean_text:
            clean_text = final_st.get("clean_response", "")

        # Dispatch report to Raon's inbox for persistent tracking (no duplicate wake-up since Raon is synchronously awaiting)
        self._dispatch_worker_report_to_raon(
            worker_key=worker_key,
            task_prompt=prompt,
            response_text=clean_text,
            trigger_relay=False
        )

        return {
            "ok": res.get("ok", False),
            "worker": worker_key,
            "model": final_st.get("model", ""),
            "status": final_st.get("status", "idle"),
            "result": clean_text,
            "error": final_st.get("model_error"),
            "raw_terminal_preview": (final_st.get("raw_terminal", "") or "")[-500:],
        }


    def approve(self, name: str, decision: str = "approve", custom_key: Optional[str] = None) -> Dict[str, Any]:
        """Approve or deny an interactive prompt (enter, esc, y, n)."""
        worker_key = self._normalize_worker_name(name)
        target = self._resolve_target(worker_key)

        is_claude = "claude" in worker_key
        key = "enter"
        if custom_key:
            key = custom_key
        elif decision in ("approve", "yes", "y"):
            key = "enter" if not is_claude else "y"
        elif decision in ("reject", "no", "deny", "n"):
            key = "esc" if not is_claude else "n"

        res = self._run_cmd(["agent", "send-keys", target, key], timeout=5)
        if is_claude and key in ("y", "n"):
            time.sleep(0.2)
            self._run_cmd(["agent", "send-keys", target, "enter"], timeout=3)

        time.sleep(1)
        st = self.sync_worker_state(worker_key)
        self._broadcast({"event": "worker_approved", "decision": decision, "worker": st})
        return {"ok": res["ok"], "worker": st}

    def set_worker_model(self, name: str, model_id: str) -> Dict[str, Any]:
        """
        Dynamically update worker's model/provider and restart the Herdr agent pane.
        Looks up model_id in custom_providers.json to find matching provider (minimax, opencode-go, openrouter, chatgpt, etc.).
        """
        self.ensure_server()
        with self._state_lock:
            worker = self._workers.get(name)
            if not worker:
                raise ValueError(f"Unknown worker '{name}'")
            kind = worker.get("kind", "codex")

        # 1. (provider, model) 해석 — §2.2 폴백 계층 (JSON → §1.4 폴백표 → 슬롯 기본값)
        requested_raw = (model_id or "").strip()
        prov_key, target_model_id, _explicit, _resolve_note = self._boardroom_pair(
            name, requested_raw
        )

        # presets 하위호환: 폴백으로 떨어졌을 때만 구 presets 목록을 확인한다.
        if _resolve_note:
            try:
                _presets = self._load_boardroom_providers().get("presets", {})
                _req_id = requested_raw.split(" (")[0].strip().lower()
                for _pn, _pc in _presets.items():
                    for _m in _pc.get("models", []):
                        _mid = _m.get("id") if isinstance(_m, dict) else str(_m)
                        if _req_id == _mid.lower():
                            prov_key, target_model_id = _pn, _mid
                            _resolve_note = ""
                            break
                    if not _resolve_note:
                        break
            except Exception:
                pass

        # 자격증명·표시명은 읽기 전용으로 채운다 (쓰기 금지).
        _pcfg = self._load_boardroom_providers().get("providers", {}).get(prov_key) or {}
        _spec = _BOARDROOM_SPECS.get(prov_key, {})
        base_url = _pcfg.get("base_url", "") or _spec.get("base_url", "")
        api_key = str(_pcfg.get("api_key", "") or "")
        label = _pcfg.get("label", "") or _spec.get("label", prov_key)

        # 회의실 슬롯에서 ChatGPT OAuth 구독 모델은 제외한다(계약 §1.2).
        # 카탈로그 어디에도 매칭되지 않은 구독 네이티브명이면 명시 실패한다.
        _chatgpt_models = ("gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-reserve")
        _req_id_full = requested_raw.split(" (")[0].strip().lower()
        if _req_id_full in _chatgpt_models and _resolve_note:
            return self._abort_model_switch(
                name, f"unsupported_provider:chatgpt:{target_model_id}"
            )

        # ★ [2026-10-18] pane 재바인딩을 먼저. stale pane_id 그대로 pane run에
        #   넘기면 pane_not_found 즉사한다(실측). agent/pane이 없으면 start_worker가
        #   live pane을 잡아 재바인딩한 뒤 그 pane으로 진행한다.
        pane_id = self._ensure_live_pane(name)
        if not pane_id:
            return self._abort_model_switch(
                name, f"no_live_pane:{(worker.get('model_error') or '')[-200:]}"
            )

        if kind == "codex":
            # 슬롯 전용 CODEX_HOME 렌더링 + pane 주입(I-4). 전역 config·전역등록 금지.
            slot_home = self._slot_codex_home(name, prov_key or "opencode-go")
            self._render_slot_codex_config(
                slot_home,
                prov_key or "opencode-go",
                target_model_id,
                self._boardroom_context_window(prov_key or "opencode-go"),
            )
            # 종료 명령은 살아있는 agent 타겟에만 보낸다. pane_id(wN:pM)는
            # agent prompt의 NAME 규칙에 어긋나 invalid_agent_name이 되므로
            # agent가 없을 땐 /exit를 생략한다(정리는 kill이 담당).
            # ★ [2026-10-18] agent start NAME은 정규명(codex/claude)만 쓴다.
            #   _resolve_target 반환값(pane_id 가능)을 쓰면 exit=1 즉사(실측).
            start_name = self._agent_start_name(name)
            exit_target = self._resolve_target(name)
            if exit_target and not self._is_pane_id(exit_target):
                self._run_cmd(["agent", "prompt", exit_target, "/exit"], timeout=3)
                time.sleep(0.8)
            self._kill_pane_foreground_process(pane_id)
            time.sleep(0.5)

            env_res = self._run_cmd(
                [
                    "pane",
                    "run",
                    pane_id,
                    self._boardroom_codex_env_cmd(
                        slot_home, prov_key or "opencode-go", api_key or ""
                    ),
                ],
                timeout=3,
            )
            if not env_res.get("ok"):
                return self._abort_model_switch(
                    name,
                    f"pane_env_inject_failed:{(env_res.get('stderr') or '')[-200:]}",
                )
            time.sleep(0.5)

            # 검증된 플래그만 전달한다. 미존재 플래그는 exit=2 즉사의 원인이다.
            switch_res = self._run_cmd(
                ["agent", "start", start_name, "--kind", kind, "--pane", pane_id,
                 "--timeout", "30000", "--",
                 "--dangerously-bypass-approvals-and-sandbox"],
                timeout=35,
            )
            if not switch_res.get("ok"):
                return self._abort_model_switch(
                    name,
                    f"agent_start_failed:exit={switch_res.get('code')}:"
                    f"{(switch_res.get('stderr') or switch_res.get('stdout') or '')[-300:]}",
                )
            switch_alive, switch_diag = self._probe_agent_alive(pane_id)
            if not switch_alive:
                return self._abort_model_switch(
                    name, f"agent_start_probe_failed:{switch_diag}"
                )
        else:
            # 슬롯 08은 Anthropic 네이티브 직결만 허용. LiteLLM 경유 금지(I-5).
            if not _BOARDROOM_SPECS.get(prov_key or "", {}).get("anthropic_base_url"):
                return self._abort_model_switch(
                    name, f"claude_requires_anthropic_native:{prov_key}"
                )

            # ★ [2026-10-18] claude provider 빈값 추적 결과: _boardroom_pair는
            #   항상 기본값 폴백이라 빈 provider를 낼 수 없다. 빈값의 출처는
            #   boardroom_routes 복원 경로(worker_status 기록)이며, 슬롯 실효값은
            #   _BOARDROOM_DEFAULTS 폴백(worker-claude=opencode-go)이 보장한다.
            #   agent start NAME은 정규명만 — pane_id를 쓰면 invalid_agent_name(실측).
            start_name = self._agent_start_name(name)
            exit_target = self._resolve_target(name)
            if exit_target and not self._is_pane_id(exit_target):
                self._run_cmd(["agent", "prompt", exit_target, "/exit"], timeout=3)
                time.sleep(0.8)
            self._kill_pane_foreground_process(pane_id)
            time.sleep(0.5)

            env_cmd = self._boardroom_claude_env_cmd(
                name, prov_key or "opencode-go", target_model_id, api_key or ""
            )
            env_res = self._run_cmd(["pane", "run", pane_id, env_cmd], timeout=3)
            if not env_res.get("ok"):
                return self._abort_model_switch(
                    name,
                    f"pane_env_inject_failed:{(env_res.get('stderr') or '')[-200:]}",
                )
            time.sleep(0.5)

            # ★ [2026-10-18] --settings apiKeyHelper 필수 (start_worker와 동일).
            claude_settings = str(
                self._slot_codex_home(name, prov_key or "opencode-go")
                / "claude_settings.json"
            )
            switch_res = self._run_cmd([
                "agent", "start", start_name,
                "--kind", kind,
                "--pane", pane_id,
                "--timeout", "35000",
                "--",
                "--model", target_model_id,
                "--settings", claude_settings,
                "--dangerously-skip-permissions"
            ], timeout=40)
            if not switch_res.get("ok"):
                return self._abort_model_switch(
                    name,
                    f"agent_start_failed:exit={switch_res.get('code')}:"
                    f"{(switch_res.get('stderr') or switch_res.get('stdout') or '')[-300:]}",
                )
            switch_alive, switch_diag = self._probe_agent_alive(pane_id)
            if not switch_alive:
                return self._abort_model_switch(
                    name, f"agent_start_probe_failed:{switch_diag}"
                )

        time.sleep(1.2)
        st = self.sync_worker_state(name)
        if st.get("blocked_info") and st["blocked_info"].get("type") == "folder_trust":
            self.approve(name, decision="approve")
            time.sleep(1.5)
            st = self.sync_worker_state(name)

        display_prov = label or prov_key or "Custom"
        _key_note = "" if api_key else f"no_api_key:{prov_key}"
        _model_error = _resolve_note or _key_note or None
        # ★ [2026-10-18] worker_models 영속화 — 재기동 후 상주 FAIL의 유일 원인.
        #   성공해도 boardroom_selection.json에 안 남기면 재기동 시 _BOARDROOM_DEFAULTS로
        #   되돌아간다. read-modify-write 병합 저장, models/worker_status 키 보존.
        #   저장 실패는 조용히 넘기지 않고 model_error에 기록한다.
        worker_key = self._normalize_worker_name(name)
        _persist_err = self._persist_boardroom_worker_model(
            worker_key, prov_key, target_model_id
        )
        if _persist_err:
            _model_error = _persist_err
        # ★ [2026-10-18] worker_status stale 차단. 성공 시 live 실측으로 덮어써
        #   과거 실패값(degraded/구모델/옛 error)을 즉시 걷어낸다. pane 소실→
        #   재생성 후에도 상태 표시가 과거를 붙들지 않게 한다(실측 결함).
        #   실패 기록 자체는 _model_error에 남으므로 error=None이 아니다.
        _status_err = self._record_boardroom_worker_status(
            worker_key,
            "ready" if not _model_error else "degraded",
            target_model_id,
            prov_key,
            _model_error,
        )
        if _status_err and not _model_error:
            _model_error = _status_err
        with self._state_lock:
            worker["model"] = f"{target_model_id} ({display_prov})"
            worker["provider"] = prov_key
            worker["resolved_model"] = target_model_id
            worker["requested_model"] = (model_id or "").strip()
            worker["model_saved"] = True
            worker["model_running"] = f"{target_model_id} ({display_prov})"
            worker["model_error"] = _model_error
            worker["updated_at"] = time.time()

        st["model"] = worker["model"]
        self._broadcast({"event": "worker_model_changed", "name": name, "model": worker["model"], "worker": st})
        return st

    def _apply_codex_model_config(
        self,
        model_id: str,
        prov_key: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        label: Optional[str] = None,
        codex_home: Optional[str] = None,
        context_window: int = 0,
    ):
        """슬롯 전용 CODEX_HOME에 config.toml을 렌더링한다.

        불변조건 I-1/I-2: 전역 ~/.codex/config.toml에 쓰지 않고,
        프로세스 전역 환경·레지스트리 영구 등록을 하지 않는다. api_key 인자는
        버린다 — 키 주입은 pane 범위 $env: 로만 한다.
        구 호출자와의 호환을 위해 시그니처는 유지한다.
        """
        prov = prov_key or "opencode-go"
        home = Path(codex_home) if codex_home else self._slot_codex_home(
            "worker-codex", prov
        )
        ctx = int(context_window) if context_window else self._boardroom_context_window(prov)
        try:
            self._render_slot_codex_config(home, prov, model_id, ctx)
            _logger.info(
                "[HerdrManager] Rendered slot codex config: %s (model=%s, provider=%s)",
                home / "config.toml",
                model_id,
                prov,
            )
        except Exception as e:
            _logger.error(f"[HerdrManager] Failed to render slot codex config: {e}")


    # -------------------------------------------------------------------------
    # Terminal Cleaning & Noise Filtering
    # -------------------------------------------------------------------------
    @classmethod
    def parse_terminal_output(cls, raw: str, kind: str = "codex") -> Dict[str, Any]:
        """
        Parses raw ANSI terminal output from Herdr into:
        - clean_text: Formatted user/assistant conversation
        - turns: List of {role: 'user' | 'assistant', content: str}
        - blocked_info: {type: str, question: str, options: list} if asking for user confirmation
        """
        if not raw:
            return {"clean_text": "", "turns": [], "blocked_info": None}

        # 1. Strip ANSI escape sequences
        clean = ANSI_ESCAPE_RE.sub("", raw)

        # 2. Check for blocked questions / interactive prompts
        blocked_info = None

        # 2-A. Claude OAuth Login Prompt
        oauth_start = clean.find("https://claude.com/cai/oauth/authorize")
        if oauth_start != -1 or "Paste code here if prompted" in clean:
            auth_url = ""
            if oauth_start != -1:
                rest = clean[oauth_start:]
                paste_idx = rest.find("Paste code")
                url_blob = rest[:paste_idx] if paste_idx != -1 else rest[:600]
                auth_url = re.sub(r'[\r\n\s]+', '', url_blob)
            blocked_info = {
                "type": "oauth_login",
                "provider": "claude",
                "question": "Claude Code 계정 연동(OAuth) 인증이 필요합니다. 아래 링크를 브라우저에서 열어 로그인 및 승인 후, 발급된 코드를 채팅창에 입력해주세요.",
                "auth_url": auth_url or "https://claude.com/cai/oauth",
                "options": [
                    {"id": "open_url", "label": "🌐 브라우저에서 인증 페이지 열기", "url": auth_url}
                ],
            }
        elif "Trust this folder?" in clean or "Trust this directory?" in clean or "Do you trust the files in this folder?" in clean:
            blocked_info = {
                "type": "folder_trust",
                "question": f"{kind.title()}가 현재 작업 디렉터리에 대한 접근/실행 권한 승인을 요청했습니다.",
                "options": [{"id": "approve", "label": "신뢰하고 계속 (Enter)"}, {"id": "reject", "label": "거부 (Esc)"}],
            }
        elif "Would you like to run the following command?" in clean or "Press enter to confirm or esc to cancel" in clean or "Yes, proceed" in clean:
            reason_m = re.search(r"Reason:\s*([^\n]+)", clean)
            cmd_m = re.search(r"\$\s*([^\n]+)", clean)
            desc_parts = []
            if reason_m:
                desc_parts.append(f"이유: {reason_m.group(1).strip()}")
            if cmd_m:
                desc_parts.append(f"명령: {cmd_m.group(1).strip()}")
            question = "\n".join(desc_parts) if desc_parts else f"{kind.title()}가 외부 명령 또는 파일 생성/수정 작업 승인을 요청했습니다."
            blocked_info = {
                "type": "command_approval",
                "question": question,
                "options": [{"id": "approve", "label": "승인하고 실행 (Enter)"}, {"id": "reject", "label": "거부 (Esc)"}],
            }
        elif re.search(r"Allow\s+(?:Claude to|command|execution|file write|access|this)", clean, re.I):
            m = re.search(r"(Allow[^\n]+(?:\n[^\n]+)?)", clean)
            question = m.group(1).strip() if m else "작업 승인이 필요합니다."
            blocked_info = {
                "type": "tool_approval",
                "question": question,
                "options": [{"id": "approve", "label": "승인 (Y/Enter)"}, {"id": "reject", "label": "거부 (N/Esc)"}],
            }
        elif re.search(r"(?:Do you want to run|Execute this command|Permission needed)", clean, re.I):
            blocked_info = {
                "type": "command_approval",
                "question": "외부 작업 실행 권한 승인이 필요합니다.",
                "options": [{"id": "approve", "label": "승인 (Enter)"}, {"id": "reject", "label": "거부 (Esc)"}],
            }

        # 3. Parse conversation turns from terminal output
        lines = clean.splitlines()
        turns: List[Dict[str, str]] = []
        current_role: Optional[str] = None
        current_content: List[str] = []

        skip_prefixes = (
            "PS ", "╭", "│", "╰", "Tip:", "model:", "directory:",
            "enter continue", "Run npm install", "See full release notes", ">_ OpenAI Codex",
            "Welcome to Claude Code", "Browser didn't open?", "Paste code here",
            "https://claude.com/cai/oauth", "Claude Code v2.", "? for shortcuts",
            "ctrl+c to interrupt", "esc to interrupt", "▐▛", "▝▜", "▝▝",
        )
        noise_substrings = (
            "Update installed", "Auto-update failed", "Restart to apply",
            "bypass permissions", "shift+tab to cycle", "← for agents",
            "? for shortcuts", "ctrl+c to interrupt", "esc to interrupt",
            "API Usage Billing", "Both ANTHROPIC_AUTH_TOKEN", "to use ANTHROPIC_",
            "이 작업 디렉터리는", "git 저장소는 아닙니다", "not a git repository",
            "Ask Codex to do anything", "f2 to view",
            # ★ [2026-10-18] TUI 하단 푸터 잔여물. agent read 스냅샷 꼬리에
            #   'New activity · Back to bottom · esc' 형태로 남아 clean_response에
            #   워커 출력인 것처럼 섞인다. 모델 본문에 나올 일이 없는 TUI 전용
            #   문구라 substring 필터에 추가한다.
            "New activity", "Back to bottom",
        )

        # Detect and ignore bottom idle prompt section (e.g. Ask Codex footer)
        clean_lines = []
        for l in lines:
            s = l.strip()
            if "Ask Codex to do anything" in s:
                break
            clean_lines.append(l)

        for line in clean_lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Skip header boxes, banners, ASCII art, and known prefixes
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            if any(art_char in stripped for art_char in ("██", "░░", "▓▓", "....█")):
                continue
            if re.match(r"^[\─\-\=\_\s]{3,}$", stripped):
                continue
            if any(ns in stripped for ns in noise_substrings):
                continue
            # Skip CLI timing & status footers
            if re.search(r"^[✻✔•✘]?\s*(?:Brewed|Cogitated|Worked)\s+for\b", stripped, re.I):
                continue
            if re.search(r"^Worked\s+for\s+\d+s", stripped, re.I):
                continue
            if re.search(r"^•?\s*Working\s*\(\d+s", stripped, re.I):
                continue
            if stripped in ("❯", "›", ">"):
                continue
            if "·" in stripped and ("warning" in stripped.lower() or "pm" in stripped.lower() or "am" in stripped.lower()):
                continue
            if TIME_STAMP_RE.match(stripped):
                continue

            # Check turn start
            # In Codex: '› prompt'
            # In Claude: '❯ prompt' or '> prompt'
            is_codex_user = stripped.startswith("› ")
            is_claude_user = (stripped.startswith("❯ ") or (stripped.startswith("> ") and not stripped.startswith(">_ "))) and "Paste code" not in stripped

            if is_codex_user or is_claude_user:
                u_text = stripped[2:].strip()
                if not u_text or u_text in ("Ask Codex to do anything",):
                    continue
                if current_role and current_content:
                    t_val = "\n".join(current_content).strip()
                    if t_val:
                        turns.append({"role": current_role, "content": t_val})
                    current_content = []
                current_role = "user"
                current_content.append(u_text)
                continue

            is_codex_asst = stripped.startswith("• ")
            is_claude_asst = (
                stripped.startswith("● ") or stripped.startswith("●") or
                stripped.startswith("⏺ ") or stripped.startswith("⏺") or
                stripped.startswith("• ") or stripped.startswith("•")
            )
            if (kind == "codex" and is_codex_asst) or (kind == "claude" and is_claude_asst):
                if current_role and current_content:
                    t_val = "\n".join(current_content).strip()
                    if t_val:
                        turns.append({"role": current_role, "content": t_val})
                    current_content = []
                current_role = "assistant"
                asst_text = re.sub(r"^[●⏺•]\s*", "", stripped).strip()
                if asst_text:
                    current_content.append(asst_text)
                continue

            if current_role:
                current_content.append(stripped)

        if current_role and current_content:
            t_val = "\n".join(current_content).strip()
            if t_val:
                turns.append({"role": current_role, "content": t_val})

        # Generate clean markdown summary
        clean_text_parts = []
        for t in turns:
            if t["role"] == "user":
                clean_text_parts.append(f"**대표님:** {t['content']}")
            else:
                clean_text_parts.append(f"**워커 ({kind.title()}):**\n{t['content']}")

        clean_text = "\n\n---\n\n".join(clean_text_parts) if clean_text_parts else clean.strip()

        return {
            "clean_text": clean_text,
            "turns": turns,
            "blocked_info": blocked_info,
        }

    # -------------------------------------------------------------------------
    # SSE Event Broadcasting
    # -------------------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        """Register a new SSE client subscriber queue."""
        q = queue.Queue()
        with self._state_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """Unregister an SSE client queue."""
        with self._state_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self, event_data: Dict[str, Any]) -> None:
        """Broadcast an event payload to all SSE subscribers."""
        with self._state_lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event_data)
                except Exception:
                    dead.append(q)
            for d in dead:
                if d in self._subscribers:
                    self._subscribers.remove(d)


herdr_manager = HerdrManager.get_instance()
