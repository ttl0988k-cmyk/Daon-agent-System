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
    Path(os.environ.get("USERPROFILE", str(Path.home())))
    / "AppData"
    / "Roaming"
    / "herdr"
    / "herdr.sock"
)

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

        # Register default worker slots using detected/configured custom models
        self._workers["worker-codex"] = {
            "name": "worker-codex",
            "kind": "codex",
            "model": self._detect_current_codex_model(),
            "status": "idle",
            "pane_id": "w1:p1",
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
            "pane_id": "w1:p2",
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
        """Inspect ~/.codex/config.toml to display the active custom model."""
        config_path = Path.home() / ".codex" / "config.toml"
        if config_path.exists():
            try:
                model = None
                provider = None
                for line in config_path.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if s.startswith("model ="):
                        model = s.split("=", 1)[1].strip().strip('"').strip("'")
                    elif s.startswith("model_provider ="):
                        provider = s.split("=", 1)[1].strip().strip('"').strip("'")
                if model:
                    prov_label = {
                        "minimax": "MiniMax",
                        "opencode-go": "OpenCode Go",
                        "opencode-zen": "OpenCode Zen",
                        "openrouter": "OpenRouter",
                        "qwen-token-plan": "Qwen",
                    }.get(provider or "", provider or "Custom")
                    return f"{model} ({prov_label})"
            except Exception:
                pass
        return "MiniMax-M3.1-Flash-Preview (MiniMax)"

    def _detect_current_claude_model(self) -> str:
        """Return the default custom model for Claude Code (via LiteLLM proxy)."""
        return "deepseek-v4.1-flash (OpenCode Go)"

    def ensure_litellm_gateway(self) -> bool:
        """Ensure LiteLLM proxy is running on port 4000 to bridge Claude Code to custom providers."""
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:4000/health/liveliness")
            with urllib.request.urlopen(req, timeout=1.2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass

        import sys
        worker_plugin_dir = Path(r"c:\daon\Daon agent System\plugins\harness-worker")
        if str(worker_plugin_dir) not in sys.path:
            sys.path.insert(0, str(worker_plugin_dir))
        try:
            import worker
            worker.write_litellm_config()
            res = worker.gateway_up(wait_seconds=15)
            _logger.info(f"[HerdrManager] Started LiteLLM gateway: {res}")
            return bool(res.get("ok"))
        except Exception as e:
            _logger.error(f"[HerdrManager] Failed to launch LiteLLM gateway: {e}")
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
                    # Prefer agent name if exists, else pane_id
                    return ag.get("name") or pane_id

        # Fallback to configured pane_id in local worker state
        with self._state_lock:
            w = self._workers.get(norm_key)
            if w and w.get("pane_id"):
                return w["pane_id"]

        return "w1:p3" if target_kind == "claude" else "w1:p1"

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
                    worker["pane_id"] = pane_id

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

        target_pane = None
        if panes_res["ok"] and panes_res["json"]:
            all_panes = [p.get("pane_id") for p in panes_res["json"].get("result", {}).get("panes", []) if p.get("pane_id")]
            for p in all_panes:
                if p not in used_panes:
                    target_pane = p
                    break

        if not target_pane:
            # Need to split a pane to create a new one
            split_from = "w1:p1"
            if panes_res["ok"] and panes_res["json"]:
                panes = panes_res["json"].get("result", {}).get("panes", [])
                if panes:
                    split_from = panes[-1].get("pane_id", "w1:p1")
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
                target_pane = "w1:p2" if kind == "claude" else "w1:p1"

        herdr_agent_name = "claude" if kind == "claude" else "codex"
        if kind == "claude":
            self.ensure_litellm_gateway()
            env_cmd = (
                '$env:ANTHROPIC_BASE_URL="http://127.0.0.1:4000"; '
                '$env:ANTHROPIC_API_KEY="sk-daon-harness-worker"; '
                '$env:ANTHROPIC_SMALL_FAST_MODEL="claude-haiku-4-5"; '
                '$env:CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT="1"'
            )
            self._run_cmd(["pane", "run", target_pane, env_cmd], timeout=5)
            time.sleep(0.5)

            # Determine alias based on model
            claude_model = self._workers.get(worker_key, {}).get("model", "")
            claude_alias = "claude-oc"
            if "minimax" in claude_model.lower():
                claude_alias = "claude-minimax"
            elif "openrouter" in claude_model.lower():
                claude_alias = "claude-openrouter"
            elif "qwen" in claude_model.lower():
                claude_alias = "claude-qwen"
            elif "opencode-zen" in claude_model.lower() or "zen" in claude_model.lower():
                claude_alias = "claude-zen"
            elif "opencode" in claude_model.lower():
                claude_alias = "claude-oc"

            self._run_cmd([
                "agent", "start", herdr_agent_name,
                "--kind", kind,
                "--pane", target_pane,
                "--timeout", "35000",
                "--",
                "--model", claude_alias,
                "--dangerously-skip-permissions",
                "--permission-mode", "bypassPermissions"
            ], timeout=40)
        else:
            self._run_cmd([
                "agent", "start", herdr_agent_name,
                "--kind", kind,
                "--pane", target_pane,
                "--timeout", "30000",
                "--",
                "--dangerously-bypass-approvals-and-sandbox",
                "--ask-for-approval", "never"
            ], timeout=35)
        
        with self._state_lock:
            if worker_key in self._workers:
                self._workers[worker_key]["pane_id"] = target_pane

        time.sleep(1.5)
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
                send_agent_message(
                    sender=sender_label,
                    recipient="raon",
                    body=synthetic_body
                )
                dispatched.append({
                    "recipient": "raon",
                    "task": task_summary,
                    "body": synthetic_body
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

        if "claude" in worker_key:
            self.ensure_litellm_gateway()

        curr = self.sync_worker_state(worker_key)
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
                self._run_cmd(["agent", "prompt", target, worker_prompt, "--wait", "--timeout", "180000"], timeout=200)

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

    def execute_worker_task(self, name: str = "worker-codex", prompt: str = "", timeout: int = 120) -> Dict[str, Any]:
        """
        Synchronously dispatch a task to an external coding worker (Codex / Claude)
        and wait for completion. Used by Raon and other orchestration agents.
        """
        self.ensure_server()
        worker_key = self._normalize_worker_name(name)
        target = self._resolve_target(worker_key)

        if "claude" in worker_key:
            self.ensure_litellm_gateway()

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
            "raw_terminal_preview": (final_st.get("raw_terminal", "") or "")[-500:]
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

        # 1. Resolve Provider and Credentials from custom_providers.json
        prov_key = None
        base_url = None
        api_key = None
        label = None

        candidate_paths = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "DAON Agent System" / "data" / "custom_providers.json",
            Path(r"c:\daon\Daon agent System\data\custom_providers.json"),
        ]

        cp_data = {}
        for p in candidate_paths:
            if p.exists():
                try:
                    cp_data = json.loads(p.read_text(encoding="utf-8-sig"))
                    if cp_data.get("providers"):
                        break
                except Exception:
                    pass

        providers = cp_data.get("providers", {})
        target_model_id = (model_id or "").strip()

        # Check custom providers first (user-registered with active API keys)
        # Prioritize matching provider by name first (e.g. minimax -> minimax, qwen -> qwen-token-plan)
        # to avoid proxy providers (like opencode-go) that do not support the /responses wire protocol for non-deepseek models
        tl = target_model_id.lower()
        provider_items = list(providers.items())
        if "minimax" in tl:
            provider_items.sort(key=lambda x: 0 if x[0] == "minimax" else 1)
        elif "qwen" in tl:
            provider_items.sort(key=lambda x: 0 if "qwen" in x[0] else 1)
        elif "zen" in tl:
            provider_items.sort(key=lambda x: 0 if x[0] == "opencode-zen" else 1)
        elif "deepseek" in tl or "go" in tl:
            provider_items.sort(key=lambda x: 0 if x[0] == "opencode-go" else 1)

        for p_name, p_cfg in provider_items:
            m_list = p_cfg.get("models", [])
            for m in m_list:
                m_id = m.get("id") if isinstance(m, dict) else str(m)
                m_lbl = m.get("label") if isinstance(m, dict) else ""
                if target_model_id.lower() in (m_id.lower(), m_lbl.lower()):
                    prov_key = p_name
                    base_url = p_cfg.get("base_url", "")
                    api_key = p_cfg.get("api_key", "")
                    label = p_cfg.get("label", p_name.title())
                    target_model_id = m_id
                    break
            if prov_key:
                break

        # Check presets if not found in custom providers
        if not prov_key:
            presets = cp_data.get("presets", {})
            for p_name, p_cfg in presets.items():
                m_list = p_cfg.get("models", [])
                for m in m_list:
                    m_id = m.get("id") if isinstance(m, dict) else str(m)
                    if target_model_id.lower() == m_id.lower():
                        prov_key = p_name
                        base_url = p_cfg.get("base_url", "")
                        label = p_cfg.get("label", p_name.title())
                        target_model_id = m_id
                        break
                if prov_key:
                    break

        # Check known chatgpt native models
        chatgpt_models = ("gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-reserve")
        is_chatgpt = any(target_model_id.lower() == cm.lower() for cm in chatgpt_models)

        pane_id = worker.get("pane_id", "w1:p1") or "w1:p1"

        if kind == "codex":
            self._apply_codex_model_config(
                target_model_id,
                prov_key=prov_key if not is_chatgpt else None,
                base_url=base_url if not is_chatgpt else None,
                api_key=api_key if not is_chatgpt else None,
                label=label if not is_chatgpt else None,
            )
            self._run_cmd(["agent", "prompt", name, "/exit"], timeout=3)
            time.sleep(0.8)
            self._kill_pane_foreground_process(pane_id)
            time.sleep(0.5)

            if prov_key and api_key:
                env_key = f"{prov_key.upper().replace('-', '_')}_API_KEY"
                self._run_cmd(["pane", "run", pane_id, f'$env:{env_key}="{api_key}"'], timeout=3)
                time.sleep(0.5)

            self._run_cmd(["agent", "start", name, "--kind", kind, "--pane", pane_id, "--timeout", "30000"], timeout=35)
        else:
            # Claude: map provider to LiteLLM proxy alias
            claude_alias = "claude-oc"
            if prov_key == "minimax":
                claude_alias = "claude-minimax"
            elif prov_key == "openrouter":
                claude_alias = "claude-openrouter"
            elif prov_key == "qwen-token-plan":
                claude_alias = "claude-qwen"
            elif prov_key == "opencode-zen":
                claude_alias = "claude-zen"
            elif prov_key == "opencode-go":
                claude_alias = "claude-oc"

            self.ensure_litellm_gateway()
            self._run_cmd(["agent", "prompt", name, "/exit"], timeout=3)
            time.sleep(0.8)
            self._kill_pane_foreground_process(pane_id)
            time.sleep(0.5)

            env_cmd = (
                '$env:ANTHROPIC_BASE_URL="http://127.0.0.1:4000"; '
                '$env:ANTHROPIC_API_KEY="sk-daon-harness-worker"; '
                '$env:ANTHROPIC_SMALL_FAST_MODEL="claude-haiku-4-5"; '
                '$env:CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT="1"'
            )
            self._run_cmd(["pane", "run", pane_id, env_cmd], timeout=3)
            time.sleep(0.5)

            self._run_cmd([
                "agent", "start", name,
                "--kind", kind,
                "--pane", pane_id,
                "--timeout", "35000",
                "--",
                "--model", claude_alias,
                "--dangerously-skip-permissions"
            ], timeout=40)

        time.sleep(1.2)
        st = self.sync_worker_state(name)
        if st.get("blocked_info") and st["blocked_info"].get("type") == "folder_trust":
            self.approve(name, decision="approve")
            time.sleep(1.5)
            st = self.sync_worker_state(name)

        display_prov = label or ("ChatGPT OAuth" if is_chatgpt else prov_key or "Custom")
        with self._state_lock:
            worker["model"] = f"{target_model_id} ({display_prov})"
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
    ):
        """Update ~/.codex/config.toml to use the selected provider and model."""
        config_path = Path.home() / ".codex" / "config.toml"
        if not config_path.exists():
            return

        try:
            content = config_path.read_text(encoding="utf-8")
        except Exception as e:
            _logger.error(f"[HerdrManager] Failed to read codex config: {e}")
            return

        lines = content.splitlines()

        # Ensure approval_policy = never and sandbox_mode = danger-full-access are preserved at the top
        new_header = [
            'approval_policy = "never"',
            'sandbox_mode = "danger-full-access"',
        ]
        skip_header_keys = ("approval_policy =", "sandbox_mode =", "model =", "model_provider =", "model_context_window =")
        filtered_lines = []
        for line in lines:
            if any(line.strip().startswith(k) for k in skip_header_keys):
                continue
            filtered_lines.append(line)

        if prov_key and base_url:
            env_key = f"{prov_key.upper().replace('-', '_')}_API_KEY"
            if api_key:
                os.environ[env_key] = api_key
                try:
                    subprocess.run(["setx", env_key, api_key], capture_output=True)
                except Exception:
                    pass

            new_header.append(f'model = "{model_id}"')
            new_header.append(f'model_provider = "{prov_key}"')
            new_header.append("model_context_window = 128000")

            # Ensure [model_providers.<prov_key>] section exists
            full_text = "\n".join(filtered_lines)
            prov_section_header = f"[model_providers.{prov_key}]"
            if prov_section_header not in full_text:
                extra_headers = ""
                if prov_key in ("opencode-go", "opencode-zen"):
                    extra_headers = '\nhttp_headers = { "x-opencode-session" = "daon-harness-worker" }'
                prov_block = f"""
{prov_section_header}
name = "{label or prov_key}"
base_url = "{base_url}"
env_key = "{env_key}"
wire_api = "responses"
request_max_retries = 2
stream_idle_timeout_ms = 60000{extra_headers}
"""
                full_text += "\n" + prov_block
                filtered_lines = full_text.splitlines()
        else:
            # Native ChatGPT OAuth model
            new_header.append(f'model = "{model_id}"')

        final_content = "\n".join(new_header) + "\n" + "\n".join(filtered_lines)
        try:
            config_path.write_text(final_content, encoding="utf-8")
            _logger.info(f"[HerdrManager] Updated Codex config.toml to model={model_id}, provider={prov_key}")
        except Exception as e:
            _logger.error(f"[HerdrManager] Failed to write codex config.toml: {e}")


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
        elif "Trust this folder?" in clean or "Trust this directory?" in clean:
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
