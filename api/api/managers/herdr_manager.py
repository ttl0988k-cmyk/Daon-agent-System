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
        self._state_lock = threading.Lock()
        self._bg_threads: Dict[str, threading.Thread] = {}

        # Register default worker slots
        self._workers["worker-codex"] = {
            "name": "worker-codex",
            "kind": "codex",
            "model": "GPT-6-Luna (Free OAuth)",
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
            "model": "DeepSeek-v4.1-Flash (Proxy)",
            "status": "not_started",
            "pane_id": "",
            "last_prompt": "",
            "clean_response": "",
            "raw_terminal": "",
            "blocked_info": None,
            "turns": [],
            "updated_at": time.time(),
        }

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
        res = self._run_cmd(["agent", "get", name], timeout=5)
        with self._state_lock:
            worker = self._workers.get(name)
            if not worker:
                return {"name": name, "status": "unknown"}

            agent_status = "unknown"
            if res["ok"] and res["json"] and "result" in res["json"]:
                agent_info = res["json"]["result"].get("agent", {})
                agent_status = agent_info.get("agent_status", "unknown")
                pane_id = agent_info.get("pane_id", worker.get("pane_id", ""))
                worker["pane_id"] = pane_id

            # Read terminal snapshot
            read_res = self._run_cmd(["agent", "read", name, "--lines", "60"], timeout=5)
            if read_res["ok"]:
                raw = read_res["stdout"]
                worker["raw_terminal"] = raw
                parsed = self.parse_terminal_output(raw, worker.get("kind", "codex"))
                worker["clean_response"] = parsed["clean_text"]
                worker["blocked_info"] = parsed["blocked_info"]
                worker["turns"] = parsed["turns"]

                # 🔴 문제 1 해결: blocked는 실제 승인 질문(blocked_info)이 감지되었을 때만 세트!
                if parsed["blocked_info"]:
                    worker["status"] = "blocked"
                elif agent_status == "blocked":
                    # Herdr는 blocked로 보지만 파서에는 승인 문구가 없음
                    # (Codex TUI 스크롤백 'New activity · ↓ Back to bottom · esc' 상태이거나 대기 오탐)
                    _logger.info(f"[HerdrManager] Worker {name} reported blocked by Herdr but no approval prompt found. Escaping scrollback with esc and demoting to idle.")
                    self._run_cmd(["agent", "send-keys", name, "esc"], timeout=3)
                    worker["status"] = "idle"
                else:
                    worker["status"] = agent_status
            else:
                worker["status"] = "idle" if agent_status == "blocked" else agent_status

            worker["updated_at"] = time.time()
            return dict(worker)

    def start_worker(self, name: str = "worker-codex", kind: str = "codex", cwd: str = r"C:\daon") -> Dict[str, Any]:
        """Start or attach worker in Herdr."""
        self.ensure_server()

        # Check if agent already exists
        chk = self._run_cmd(["agent", "get", name], timeout=5)
        if chk["ok"]:
            return self.sync_worker_state(name)

        # Get or create pane
        panes_res = self._run_cmd(["pane", "list"], timeout=5)
        pane_id = "w1:p1"
        if panes_res["ok"] and panes_res["json"]:
            panes = panes_res["json"].get("result", {}).get("panes", [])
            if panes:
                pane_id = panes[0].get("pane_id", "w1:p1")

        # Start agent
        start_res = self._run_cmd(["agent", "start", name, "--kind", kind, "--pane", pane_id], timeout=15)
        
        # Check if startup asked for folder trust
        time.sleep(1)
        st = self.sync_worker_state(name)
        if st.get("blocked_info") and st["blocked_info"].get("type") == "folder_trust":
            # Auto trust workspace folder on start
            _logger.info(f"[HerdrManager] Auto-approving folder trust for {name}")
            self.approve(name, decision="approve")
            time.sleep(1.5)
            st = self.sync_worker_state(name)

        self._broadcast({"event": "worker_started", "worker": st})
        return st

    def prompt_worker(self, name: str, prompt: str) -> Dict[str, Any]:
        """Submit a prompt to the worker asynchronously and track in background thread."""
        self.ensure_server()
        # Pre-check: if worker is stuck in spurious scrollback blocked state, clear it with esc
        curr = self.sync_worker_state(name)
        if curr.get("status") == "blocked" and not curr.get("blocked_info"):
            self._run_cmd(["agent", "send-keys", name, "esc"], timeout=3)
            time.sleep(0.3)

        prompt = prompt.replace("\r\n", "\n").replace("\r", "\n")

        with self._state_lock:
            worker = self._workers.get(name)
            if not worker:
                raise ValueError(f"Unknown worker '{name}'")
            worker["status"] = "working"
            worker["last_prompt"] = prompt
            worker["blocked_info"] = None
            worker["updated_at"] = time.time()

        self._broadcast({"event": "worker_status", "name": name, "status": "working", "prompt": prompt})

        # Submit prompt via herdr agent prompt in a background thread
        def _prompt_thread():
            # Submit prompt
            res = self._run_cmd(["agent", "prompt", name, prompt, "--wait", "--timeout", "180000"], timeout=200)
            # Sync final state
            final_st = self.sync_worker_state(name)
            self._broadcast({"event": "worker_done", "worker": final_st})

        t = threading.Thread(target=_prompt_thread, daemon=True)
        self._bg_threads[name] = t
        t.start()

        return self.get_worker(name)

    def execute_worker_task(self, name: str = "worker-codex", prompt: str = "", timeout: int = 120) -> Dict[str, Any]:
        """
        Synchronously dispatch a task to an external coding worker (Codex / Claude)
        and wait for completion. Used by Raon and other orchestration agents.
        """
        self.ensure_server()
        with self._state_lock:
            worker = self._workers.get(name)
            if not worker:
                if "claude" in name.lower():
                    name = "worker-claude"
                else:
                    name = "worker-codex"
                worker = self._workers.get(name)

            if not worker:
                raise ValueError(f"Unknown worker '{name}'")

            # Check if agent is alive in Herdr; if not, start it
            chk = self._run_cmd(["agent", "get", name], timeout=5)
            if not chk["ok"]:
                _logger.info(f"[HerdrManager] Worker {name} not active in Herdr. Starting...")
                self.start_worker(name=name, kind=worker.get("kind", "codex"))

            # Pre-check spurious blocked state
            curr = self.sync_worker_state(name)
            if curr.get("status") == "blocked" and not curr.get("blocked_info"):
                self._run_cmd(["agent", "send-keys", name, "esc"], timeout=3)
                time.sleep(0.3)

            prompt = prompt.replace("\r\n", "\n").replace("\r", "\n")

            worker["status"] = "working"
            worker["last_prompt"] = prompt
            worker["blocked_info"] = None
            worker["updated_at"] = time.time()

        self._broadcast({"event": "worker_status", "name": name, "status": "working", "prompt": prompt})

        timeout_ms = str(max(10, timeout) * 1000)
        res = self._run_cmd(["agent", "prompt", name, prompt, "--wait", "--timeout", timeout_ms], timeout=timeout + 20)

        final_st = self.sync_worker_state(name)
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

        return {
            "ok": res.get("ok", False),
            "worker": name,
            "model": final_st.get("model", ""),
            "status": final_st.get("status", "idle"),
            "result": clean_text,
            "raw_terminal_preview": (final_st.get("raw_terminal", "") or "")[-500:]
        }

    def approve(self, name: str, decision: str = "approve", custom_key: Optional[str] = None) -> Dict[str, Any]:
        """Approve or deny an interactive prompt (enter, esc, y, n)."""
        key = "enter"
        if custom_key:
            key = custom_key
        elif decision in ("approve", "yes", "y"):
            key = "enter"
        elif decision in ("reject", "no", "deny", "n"):
            key = "esc"

        res = self._run_cmd(["agent", "send-keys", name, key], timeout=5)
        time.sleep(1)
        st = self.sync_worker_state(name)
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
        for p_name, p_cfg in providers.items():
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

        if kind == "codex":
            self._apply_codex_model_config(
                target_model_id,
                prov_key=prov_key if not is_chatgpt else None,
                base_url=base_url if not is_chatgpt else None,
                api_key=api_key if not is_chatgpt else None,
                label=label if not is_chatgpt else None,
            )

        # 2. Restart worker in Herdr
        # Exit current running agent in pane
        self._run_cmd(["agent", "prompt", name, "/exit"], timeout=5)
        time.sleep(1.2)
        pane_id = worker.get("pane_id", "w1:p1") or "w1:p1"
        self._run_cmd(["pane", "send-keys", pane_id, "c-c"], timeout=3)
        time.sleep(0.5)

        # Inject environment variable into pane shell so codex has the API key
        if prov_key and api_key:
            env_key = f"{prov_key.upper().replace('-', '_')}_API_KEY"
            self._run_cmd(["pane", "send-keys", pane_id, f'$env:{env_key}="{api_key}"', "enter"], timeout=3)
            time.sleep(0.5)

        # Start agent again with updated model
        self._run_cmd(["agent", "start", name, "--kind", kind, "--pane", pane_id], timeout=15)
        time.sleep(1.2)

        # Check folder trust prompt auto-approval
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

        # Remove existing model, model_provider, model_context_window at the top
        new_header = []
        skip_header_keys = ("model =", "model_provider =", "model_context_window =")
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
                if prov_key == "opencode-go":
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

        # 2. Check for blocked questions
        blocked_info = None
        if "Trust this folder?" in clean or "Trust this directory?" in clean:
            blocked_info = {
                "type": "folder_trust",
                "question": "Codex가 현재 작업 디렉터리에 대한 접근/실행 권한 승인을 요청했습니다.",
                "options": [{"id": "approve", "label": "신뢰하고 계속 (Enter)"}, {"id": "reject", "label": "거부 (Esc)"}],
            }
        elif "Would you like to run the following command?" in clean or "Press enter to confirm or esc to cancel" in clean or "Yes, proceed" in clean:
            # Extract reason and command if present
            reason_m = re.search(r"Reason:\s*([^\n]+)", clean)
            cmd_m = re.search(r"\$\s*([^\n]+)", clean)
            desc_parts = []
            if reason_m:
                desc_parts.append(f"이유: {reason_m.group(1).strip()}")
            if cmd_m:
                desc_parts.append(f"명령: {cmd_m.group(1).strip()}")
            question = "\n".join(desc_parts) if desc_parts else "Codex가 외부 명령 또는 파일 생성/수정 작업 승인을 요청했습니다."
            blocked_info = {
                "type": "command_approval",
                "question": question,
                "options": [{"id": "approve", "label": "승인하고 실행 (Enter)"}, {"id": "reject", "label": "거부 (Esc)"}],
            }
        elif re.search(r"Allow\s+(?:command|execution|file write|access|this)", clean, re.I):
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
        # In Codex:
        # User prompt begins with '› <prompt>'
        # Model output begins with '• <answer>'
        lines = clean.splitlines()
        turns: List[Dict[str, str]] = []
        current_role: Optional[str] = None
        current_content: List[str] = []

        skip_prefixes = (
            "PS ",
            "╭",
            "│",
            "╰",
            "Tip:",
            "› Ask Codex",
            "model:",
            "directory:",
            "enter continue",
            "Run npm install",
            "See full release notes",
            ">_ OpenAI Codex",
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Skip header boxes and boilerplate
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            if "·" in stripped and ("warning" in stripped.lower() or "pm" in stripped.lower() or "am" in stripped.lower()):
                continue
            if TIME_STAMP_RE.match(stripped):
                continue

            # Check turn start
            if stripped.startswith("› "):
                # Finish previous
                if current_role and current_content:
                    turns.append({"role": current_role, "content": "\n".join(current_content).strip()})
                    current_content = []
                current_role = "user"
                current_content.append(stripped[2:].strip())
            elif stripped.startswith("• "):
                if current_role and current_content:
                    turns.append({"role": current_role, "content": "\n".join(current_content).strip()})
                    current_content = []
                current_role = "assistant"
                current_content.append(stripped[2:].strip())
            else:
                if current_role:
                    current_content.append(stripped)

        if current_role and current_content:
            turns.append({"role": current_role, "content": "\n".join(current_content).strip()})

        # Generate clean markdown summary
        clean_text_parts = []
        for t in turns:
            if t["role"] == "user":
                clean_text_parts.append(f"**대표님:** {t['content']}")
            else:
                clean_text_parts.append(f"**워커 ({kind}):**\n{t['content']}")

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
